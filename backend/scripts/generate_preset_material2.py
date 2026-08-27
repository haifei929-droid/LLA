"""Generate the first full-length preset material (Spec 3.1: ~15-20 min slow
clear English). Original text, per-sentence TTS synthesis, precise
timestamps, natural semantic part boundaries at paragraph ends, and a
concatenated WAV."""

from __future__ import annotations

import asyncio
import json
import struct
import subprocess
import wave
from pathlib import Path

# Three semantically complete paragraphs (one per Part, Spec 3.2).
PART1 = [
    "Rain begins far above our heads, high in the sky.",
    "The sun warms the oceans and the seas every day.",
    "Warm water slowly turns into vapor and rises into the air.",
    "This invisible vapor travels upward with the warm wind.",
    "High in the sky the air grows colder and colder.",
    "The cold air makes the vapor form tiny drops of water.",
    "Millions of tiny drops gather together and become clouds.",
    "The clouds drift across the land, pushed by the wind.",
    "When the drops grow heavy, they fall back to the ground.",
    "That falling water is the rain we see and feel.",
    "Rain often starts as snow or ice high in the clouds.",
    "The ice melts on the way down and becomes liquid again.",
    "Some rain falls into the ocean, not far from where it began.",
    "Other rain travels many miles before it reaches the ground.",
    "The whole journey is called the water cycle.",
    "The water cycle has no beginning and no end.",
    "The same water has been moving around the earth for millions of years.",
    "The water you drink today may have fallen as rain long ago.",
    "Clouds are not all the same, and neither is rain.",
    "Soft rain falls slowly and soaks quietly into the soil.",
    "Heavy rain pours down quickly and fills the streams.",
    "The shape of the land decides where the rain will go.",
    "Mountains catch the clouds and cool them quickly.",
    "That is why one side of a mountain may be wet and green.",
    "The other side may stay dry and brown for most of the year.",
    "Warm air holds more water than cold air does.",
    "When warm air meets a cold mountain, it has to rise.",
    "As it rises, it cools, and the rain begins to fall.",
    "This is how many of the world's great forests stay alive.",
    "Even deserts were not always dry places.",
    "Long ago, some deserts were covered by lakes and grass.",
    "The climate changed slowly, and the rain stopped coming.",
    "When the rain stops, the land slowly turns to dust.",
    "But rain can also return to a dry land.",
    "A few good seasons of rain can bring life back to the soil.",
    "Seeds that waited for years will wake up and grow.",
    "So rain is both a gentle visitor and a powerful force.",
    "It can feed a single flower or shape a whole valley.",
    "Understanding rain means understanding water itself.",
    "And water is the most common and most precious substance on earth.",
    # --- extended for 15-20 min target ---
    "Some clouds are thin and white and bring no rain at all.",
    "Others are dark and heavy and carry storms inside them.",
    "The shape of a cloud can tell us what weather is coming.",
    "Flat grey clouds usually mean steady rain for many hours.",
    "Tall clouds that rise like towers often bring sudden storms.",
    "Rain is not the only way water returns to the earth.",
    "Fog forms when warm moist air cools near the ground.",
    "Dew appears on grass in the morning after a cool night.",
    "Frost is frozen dew that sparkles in the winter sun.",
    "Snow, ice, fog, and dew are all cousins of the rain.",
    "They all come from the same endless journey of water.",
    "Every season has its own way of delivering water to the land.",
    # --- extended for 15-20 min target ---
    "Look at the sky after a storm, and you will see the cycle continue.",
    "The sun comes out, the puddles dry, and the water rises again.",
    "So the journey of a raindrop never truly ends.",
]

PART2 = [
    "When rain reaches the ground, its work has just begun.",
    "Some of the water sinks slowly into the earth.",
    "This water becomes part of the ground beneath our feet.",
    "Deep underground, it flows through cracks in the rock.",
    "This hidden water is called groundwater.",
    "Groundwater can travel very slowly, only a few meters a year.",
    "Some of it stays underground for thousands of years.",
    "Other rain flows across the surface of the land.",
    "Small streams join together and become larger rivers.",
    "A river is really the memory of many rains.",
    "Rivers carry soil, seeds, and minerals from the highlands.",
    "This rich material settles on the plains during floods.",
    "Farmers call this gift of the river new soil.",
    "Without rivers, many plains would be dry and empty.",
    "Rain also shapes the land in slow and steady ways.",
    "Drops of rain wear away the hardest rock over time.",
    "They cut valleys, carve canyons, and round off mountains.",
    "A single drop seems too small to matter.",
    "But billions of drops, falling year after year, change everything.",
    "Water is the great sculptor of the earth's surface.",
    "Rain brings life to plants of every kind.",
    "Green leaves use sunlight, air, and water to make food.",
    "This process is the start of nearly all food chains.",
    "Animals eat the plants, and other animals eat them.",
    "In this way, rain supports life that never touches water directly.",
    "Forests are especially good at catching and keeping rain.",
    "Tree roots hold the soil and slow the flow of water.",
    "Forest leaves break the force of heavy rain.",
    "When forests are cut down, the land loses this protection.",
    "Rain then runs off quickly, carrying the soil away.",
    "Land without trees can turn from green to brown in a few years.",
    "Wetlands do a similar job on flat land.",
    "Marshes and swamps store floodwater like giant sponges.",
    "They clean the water slowly as it passes through.",
    "Many birds and fish depend on these quiet places.",
    "When we drain wetlands, we lose their protection.",
    "The rain keeps falling, but the land cannot hold it anymore.",
    "That is why floods can come even after a short storm.",
    "In healthy landscapes, the rain is kept and released slowly.",
    "Rivers stay full through the dry season because of this storage.",
    # --- extended for 15-20 min target ---
    "Rivers do not end at the sea in every case.",
    "Some rivers spread out into wide flat deltas.",
    "Deltas are made of soil that the river has carried for miles.",
    "These low green lands are often the most fertile places on earth.",
    "Lakes play a quieter part in the water story.",
    "A lake catches the rain and releases it slowly.",
    "Many rivers begin their journey in a mountain lake.",
    "Lakes also cool the air around them in summer.",
    "People have always gathered around lakes for water and food.",
    "Groundwater and rivers are connected in a hidden way.",
    "When people pump too much groundwater, rivers can dry up.",
    "Taking care of one means taking care of the other.",
    # --- extended for 15-20 min target ---
    "Every river tells the story of the land it crosses.",
    "Reading that story helps us care for the whole watershed.",
    "A healthy watershed is a gift to every community downstream.",
]

PART3 = [
    "People have watched the rain for as long as we have existed.",
    "Early farmers studied the sky to know when to plant.",
    "In some lands, one season brings almost all the rain.",
    "The people of those lands planned their whole year around it.",
    "They built homes that could catch and store the water.",
    "They dug canals to carry water to distant fields.",
    "Some ancient cities grew powerful because of their water systems.",
    "When the rains failed, the cities weakened and fell.",
    "Weather records show that rain has never been steady.",
    "Some years bring too much water, and others too little.",
    "This natural change is part of life on earth.",
    "But the climate is warming, and the pattern is changing.",
    "Warm air carries more water, so storms can be stronger.",
    "Some places now get heavier rain in shorter time.",
    "Other places get longer dry periods between rains.",
    "Cities face a special problem with heavy rain.",
    "Streets and roofs do not let water sink into the ground.",
    "The rain must flow away through drains and pipes.",
    "When the rain comes faster than the pipes can carry it, streets flood.",
    "Engineers are designing new ways to manage this water.",
    "Some cities are building parks that can hold floodwater.",
    "Others are planting trees along the streets to absorb rain.",
    "Rooftop gardens catch rain before it reaches the ground.",
    "Every small action helps the city hold more water.",
    "Farmers are also adapting to the changing rain.",
    "They are choosing crops that need less water.",
    "They are saving rain in ponds for the dry months.",
    "Drip irrigation gives each plant just the water it needs.",
    "These methods use far less water than old ways.",
    "Water is too precious to waste in any season.",
    "Simple habits at home can save a surprising amount of rain.",
    "A rain barrel under the roof can hold hundreds of liters.",
    "That water can water the garden for many weeks.",
    "Fixing a dripping tap saves thousands of liters a year.",
    "Every drop that is saved stays in the local water system.",
    "We cannot control when the rain will fall.",
    "But we can control how we receive and use it.",
    "The rain will keep falling on the earth, as it always has.",
    "It will fill the rivers and wake the seeds.",
    "It will continue its endless journey through the sky and the soil.",
    "Our job is to listen to it, learn from it, and live with it wisely.",
    # --- extended for 15-20 min target ---
    "Long ago, people built great stone tanks to store rain.",
    "Some of these ancient tanks still hold water today.",
    "Their builders understood the value of every drop.",
    "Modern houses can learn from these old ideas.",
    "Schools can teach children where their water comes from.",
    "When children understand the water cycle, they protect it better.",
    "Simple signs in parks can remind people to save water.",
    "Shops can sell water-saving tools at lower prices.",
    "Governments can protect the wetlands and forests that store rain.",
    "Every person can make a small difference every day.",
    "We do not need to wait for the next generation to act.",
    "The water we save today is the rain we will need tomorrow.",
    # --- extended for 15-20 min target ---
    "The choice is always ours to make, day by day.",
    "And the sky will keep offering us its quiet gift.",
    "Let us receive it with care, and pass it on with wisdom.",
]

VOICE = "en-US-JennyNeural"
RATE = "-25%"
SILENCE_BETWEEN = 0.6  # seconds between sentences
MATERIAL_ID = "preset-002"
OUT_DIR = Path(rf"D:\codex\LLA\data\materials\{MATERIAL_ID}")
RATE_HZ = 16000


async def synth_sentence(text: str, mp3_path: Path) -> None:
    from edge_tts import Communicate

    com = Communicate(text, VOICE, rate=RATE)
    await com.save(str(mp3_path))


def mp3_to_wav(mp3_path: Path, wav_path: Path) -> float:
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-i", str(mp3_path),
            "-ac", "1", "-ar", str(RATE_HZ), "-sample_fmt", "s16",
            str(wav_path),
        ],
        check=True,
    )
    with wave.open(str(wav_path), "rb") as wav_file:
        return wav_file.getnframes() / wav_file.getframerate()


def concat_wavs(sentence_wavs: list[Path], output: Path) -> None:
    silence = struct.pack("<h", 0)
    silence_frames = int(SILENCE_BETWEEN * RATE_HZ)
    with wave.open(str(output), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE_HZ)
        for index, wav_path in enumerate(sentence_wavs):
            with wave.open(str(wav_path), "rb") as source:
                out.writeframes(source.readframes(source.getnframes()))
            if index < len(sentence_wavs) - 1:
                for _ in range(silence_frames):
                    out.writeframes(silence)


def main() -> None:
    sentences = PART1 + PART2 + PART3
    # 1-based sentence indexes where a semantic part ends (Spec 3.2).
    part_boundaries = [len(PART1), len(PART1) + len(PART2)]
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    mp3_paths = [OUT_DIR / f"sentence-{index:03d}.mp3" for index in range(len(sentences))]
    wav_paths = [OUT_DIR / f"sentence-{index:03d}.wav" for index in range(len(sentences))]

    for index, (text, mp3_path) in enumerate(zip(sentences, mp3_paths)):
        if not mp3_path.exists():
            print(f"synth {index + 1}/{len(sentences)}", flush=True)
            asyncio.run(synth_sentence(text, mp3_path))

    durations: list[float] = []
    for mp3_path, wav_path in zip(mp3_paths, wav_paths):
        if not wav_path.exists():
            durations.append(mp3_to_wav(mp3_path, wav_path))
        else:
            with wave.open(str(wav_path), "rb") as wav_file:
                durations.append(wav_file.getnframes() / wav_file.getframerate())

    full_path = Path(rf"D:\codex\LLA\data\materials\{MATERIAL_ID}.wav")
    if not full_path.exists():
        concat_wavs(wav_paths, full_path)

    timestamped: list[dict[str, object]] = []
    cursor = 0.0
    for text, duration in zip(sentences, durations):
        start = round(cursor, 3)
        end = round(cursor + duration, 3)
        timestamped.append({"text": text, "start_time": start, "end_time": end})
        cursor = end + SILENCE_BETWEEN

    payload = {
        "material_id": MATERIAL_ID,
        "title": "The Story of Rain (slow, clear, ~17 min)",
        "audio_path": f"data/materials/{MATERIAL_ID}.wav",
        "transcript": " ".join(sentences),
        "timestamped_sentences": timestamped,
        "natural_part_boundaries": part_boundaries,
    }
    (OUT_DIR / "manifest.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    words = sum(len(sentence.split()) for sentence in sentences)
    total = cursor - SILENCE_BETWEEN
    print(f"sentences={len(sentences)} words={words}")
    print(f"total duration: {total:.0f}s = {total / 60:.1f} min (wpm={words / (total / 60):.0f})")
    print(f"natural part boundaries (1-based): {part_boundaries}")
    print("manifest:", OUT_DIR / "manifest.json")


if __name__ == "__main__":
    main()
