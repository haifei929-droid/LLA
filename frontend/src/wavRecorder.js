// Browser microphone -> 16-bit PCM WAV recorder.
// The reading scorer analyzes WAV files on the server (no ffmpeg), so the
// MediaRecorder webm output is not usable; PCM is captured and encoded here.

function writeString(view, offset, text) {
  for (let i = 0; i < text.length; i++) {
    view.setUint8(offset + i, text.charCodeAt(i))
  }
}

function encodeWav(chunks, sampleRate) {
  const total = chunks.reduce((sum, chunk) => sum + chunk.length, 0)
  const buffer = new ArrayBuffer(44 + total * 2)
  const view = new DataView(buffer)
  writeString(view, 0, 'RIFF')
  view.setUint32(4, 36 + total * 2, true)
  writeString(view, 8, 'WAVE')
  writeString(view, 12, 'fmt ')
  view.setUint32(16, 16, true)
  view.setUint16(20, 1, true) // PCM
  view.setUint16(22, 1, true) // mono
  view.setUint32(24, sampleRate, true)
  view.setUint32(28, sampleRate * 2, true)
  view.setUint16(32, 2, true)
  view.setUint16(34, 16, true)
  writeString(view, 36, 'data')
  view.setUint32(40, total * 2, true)
  let offset = 44
  for (const chunk of chunks) {
    for (let i = 0; i < chunk.length; i++) {
      const sample = Math.max(-1, Math.min(1, chunk[i]))
      view.setInt16(offset, sample < 0 ? sample * 0x8000 : sample * 0x7fff, true)
      offset += 2
    }
  }
  return new Blob([buffer], { type: 'audio/wav' })
}

export function startWavRecording() {
  return navigator.mediaDevices.getUserMedia({ audio: true }).then((stream) => {
    const audioContext = new AudioContext()
    const source = audioContext.createMediaStreamSource(stream)
    const processor = audioContext.createScriptProcessor(4096, 1, 1)
    const silence = audioContext.createGain()
    silence.gain.value = 0
    const chunks = []
    let stopped = false
    source.connect(processor)
    processor.connect(silence)
    silence.connect(audioContext.destination)
    processor.onaudioprocess = (event) => {
      if (!stopped) chunks.push(new Float32Array(event.inputBuffer.getChannelData(0)))
    }
    const stop = () => new Promise((resolve) => {
      stopped = true
      processor.disconnect()
      source.disconnect()
      silence.disconnect()
      stream.getTracks().forEach((track) => track.stop())
      const blob = encodeWav(chunks, audioContext.sampleRate)
      audioContext.close()
      resolve(blob)
    })
    return { stop }
  })
}
