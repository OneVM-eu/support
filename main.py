import discord
import requests
import json
import os
import aiohttp
import datetime
import schedule
import time
import asyncio

DISCORD_TOKEN = "DISCORD BOT TOKEN"
SUPPORT_FORUM_ID = 0
STAFF_FORUM_ID = 0
MAX_TOKENS = 130000
HISTORY_FILE = "thread_histories.json"

if os.path.exists(HISTORY_FILE):
    with open(HISTORY_FILE, "r") as f:
        thread_histories = json.load(f)
else:
    thread_histories = {}

AI_MODELS = [
    {
        "name": "groq-llama",
        "endpoint": "https:/.../v1/chat/completions",
        "key": "",
        "model": "llama-3.3-70b-versatile"
    },
    {
        "name": "groq-alt",
        "endpoint": "https://.../v1/chat/completions",
        "key": "",
        "model": "llama3-70b-8192"
    },
    {
        "name": "groq-alt2",
        "endpoint": "https://.../v1/chat/completions",
        "key": "",
        "model": "deepseek-r1-distill-llama-70b"
    }
]

VISION_MODEL = {
    "name": "groq-vision",
    "endpoint": "https://.../v1/chat/completions",
    "key": "",  
    "model": "meta-llama/llama-4-scout-17b-16e-instruct"  
}

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

def call_ai_model(messages, username):
    headers = {"Content-Type": "application/json"}
    system_prompt = {
        "role": "system",
        "content": (
            f"You are OVM AI, a support assistant created by OneVM.eu to help users troubleshoot server-side technical errors—"
            f"such as Minecraft plugin issues, code bugs, or software crashes. You are currently assisting a user named '{username}'. "
            f"You must not assist with creating servers, configuring server setup, credit systems, invite rewards, or any OneVM.eu website-related content. "
            f"You must also ignore anything related to Meta and its services. Speak formally and clearly, addressing the user respectfully. "
            f"Always remind them that your responses are generated automatically and may contain inaccuracies. Encourage them to verify technical advice before acting on it."
        )
    }

    full_messages = [system_prompt] + messages

    for model in AI_MODELS:
        payload = {
            "model": model["model"],
            "messages": full_messages
        }
        headers["Authorization"] = f"Bearer {model['key']}"

        try:
            response = requests.post(model["endpoint"], headers=headers, json=payload)
            if response.status_code == 200:
                return response.json()["choices"][0]["message"]["content"]
        except Exception as e:
            print(f"❌ Error with {model['name']}: {e}")

    return "⚠️ All models are currently unavailable. Please try again later."

@client.event
async def on_ready():
    print(f"✅ Logged in as {client.user}")
    await client.change_presence(
        status=discord.Status.idle,
        activity=discord.Activity(type=discord.ActivityType.playing, name="#support")
    )

@client.event
async def on_message(message):
    if message.author == client.user:
        return

    if message.content.strip() == "!startup":
            return await message.channel.send("❌ Sadly OneVM.eu does **not** allow to change startup command for free servers.")

    if message.channel.type == discord.ChannelType.public_thread and message.channel.parent_id in [SUPPORT_FORUM_ID, STAFF_FORUM_ID]:
        thread_id = message.channel.id
        username = message.author.display_name

        if message.content.startswith(">"):
            return

        if message.content.strip() == "!forget":
            thread = message.channel
            author_is_creator = thread.owner_id == message.author.id
            user_is_admin = message.author.guild_permissions.administrator

            if author_is_creator or user_is_admin:
                thread_histories[thread_id] = []
                with open(HISTORY_FILE, "w") as f:
                    json.dump(thread_histories, f)
                await message.channel.send(f"🧹 Thread history has been purged by {username}.")
            else:
                await message.channel.send("❌ You are not authorized to purge this thread’s history.")
            return

        if thread_id not in thread_histories:
            thread_histories[thread_id] = []

        thread_histories[thread_id].append({"role": "user", "content": message.content})

        # IMAGE PROCESSING
        if message.attachments and message.attachments[0].content_type.startswith("image/"):
            image_url = message.attachments[0].url
            vision_prompt = [
                {
                    "role": "system",
                    "content": (
                        "You are a technical image interpreter helping users troubleshoot issues based on screenshots. "
                        "Describe the image in precise detail and offer insights useful for debugging."
                    )
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "Describe this image"
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": image_url
                            }
                        }
                    ]
                }
            ]
            headers = {
                "Authorization": f"Bearer {VISION_MODEL['key']}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": VISION_MODEL["model"],
                "messages": vision_prompt
            }

            try:
                response = requests.post(VISION_MODEL["endpoint"], headers=headers, json=payload)
                if response.status_code == 200:
                    image_description = response.json()["choices"][0]["message"]["content"]
                    thread_histories[thread_id].append({
                        "role": "user",
                        "content": f"[Image Analysis]: {image_description}"
                    })
                else:
                    await message.channel.send("⚠️ Vision model couldn’t process the image.")
            except Exception as e:
                print(f"❌ Vision error: {e}")
                await message.channel.send("❌ An error occurred during image analysis.")

        # TEXT FILE PROCESSING
        supported_extensions = [".txt", ".js", ".py", ".log", ".json"]
        for attachment in message.attachments:
            if any(attachment.filename.endswith(ext) for ext in supported_extensions):
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.get(attachment.url) as resp:
                            if resp.status == 200:
                                file_content = await resp.text()
                                thread_histories[thread_id].append({
                                    "role": "user",
                                    "content": f"[File: {attachment.filename}]\n{file_content}"
                                })
                            else:
                                await message.channel.send(f"❌ Failed to fetch {attachment.filename}.")
                except Exception as e:
                    print(f"❌ Error reading file: {e}")
                    await message.channel.send(f"⚠️ Could not process {attachment.filename}.")

        total_tokens = sum(len(msg["content"]) for msg in thread_histories[thread_id])
        if total_tokens > MAX_TOKENS:
            await message.channel.send(
                "🚫 Token limit reached. This thread is being locked to preserve performance. Please open a new support thread if needed."
            )
            try:
                await message.channel.edit(locked=True)
            except Exception as e:
                print(f"❌ Failed to lock thread: {e}")
            return

        reply = call_ai_model(thread_histories[thread_id], username)
        thread_histories[thread_id].append({"role": "assistant", "content": reply})

        with open(HISTORY_FILE, "w") as f:
            json.dump(thread_histories, f)

        if len(reply) > 2000:
            chunks = [reply[i:i+2000] for i in range(0, len(reply), 2000)]
            for chunk in chunks:
                await message.channel.send(chunk)
        else:
            await message.channel.send(reply)


def get_weather():
    url = "http://wttr.in/Berlin?format=j1"
    response = requests.get(url)
    weather_data = response.json()
    return weather_data

async def update_server_name(server, weather_data):
    try:
        current_time = datetime.datetime.now()
        hour = current_time.hour

        if hour >= 6 and hour < 20:  
            condition = weather_data["current_condition"][0]["weatherDesc"][0]["value"]
            print(f"Weather condition: {condition}")
            if condition == "Sunny":
                await server.edit(name="☀ OneVM.eu")
            elif condition == "Partly cloudy":
                await server.edit(name="☁ OneVM.eu")
            elif condition == "Light rain shower" or "Rain shower":
                await server.edit(name="🌧️ OneVM.eu")
        else:  
            moon_phase = get_moon_phase()
            print(f"Moon phase: {moon_phase}")
            await server.edit(name=moon_phase)
        print(f"Server name updated to: {server.name}")
    except Exception as e:
        print(f"Error updating server name: {e}")



def get_moon_phase():
    current_time = datetime.datetime.now()
    moon_phase = (current_time.day % 29)
    if moon_phase < 7:
        return "🌑 OneVM.eu"
    elif moon_phase < 14:
        return "🌒 OneVM.eu"
    elif moon_phase < 21:
        return "🌕 OneVM.eu"
    else:
        return "🌖 OneVM.eu"

async def update_server_name_async(server_id):
    server = client.get_guild(int(server_id))
    if server is None:
        print("❌ Server not found. Check the server ID and bot's guild membership.")
        return
    print(f"✅ Found server: {server.name}")
    weather_data = get_weather()
    await update_server_name(server, weather_data)


async def job():
    await update_server_name_async("SERVER ID")

@client.event
async def on_ready():
    print(f"✅ Logged in as {client.user}")
    await client.change_presence(
        status=discord.Status.idle,
        activity=discord.Activity(type=discord.ActivityType.playing, name="#support")
    )

    await job()

    # Schedule job every hour
    async def scheduler():
        while True:
            await job()
            await asyncio.sleep(3600)  # 1 hour

    asyncio.create_task(scheduler())


client.run(DISCORD_TOKEN)
