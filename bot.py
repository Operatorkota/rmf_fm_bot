print("STARTING BOT...")
import discord
from discord.ext import commands
import asyncio
import aiohttp
import requests
from bs4 import BeautifulSoup
import yt_dlp
from thefuzz import fuzz
import random
import os
import re
import copy
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import json
from datetime import datetime, timedelta
import sys
from zoneinfo import ZoneInfo
import feedparser
import time
import sys
import functools

# Zostawiamy komunikaty, ale sama redirekcja jest wyłączona
print("Logi będą wyświetlane w terminalu.")
# sys.stdout = open('bot.log', 'w', encoding='utf-8')
# sys.stderr = sys.stdout

# --- Konfiguracja Ścieżek ---
# Użyj zmiennej środowiskowej DATA_PATH, jeśli jest ustawiona.
# W przeciwnym razie użyj katalogu, w którym znajduje się skrypt bot.py.
# To zapewnia, że bot zawsze znajdzie swoje pliki, niezależnie od miejsca uruchomienia.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.getenv('DATA_PATH', SCRIPT_DIR)
print(f"Używam katalogu danych: {os.path.abspath(DATA_DIR)}")


# --- Konfiguracja ---


try:
    with open(os.path.join(DATA_DIR, 'config.json'), 'r', encoding='utf-8') as f:
        config = json.load(f)
        BOT_TOKEN = config.get("BOT_TOKEN")
        SPOTIPY_CLIENT_ID = config.get("SPOTIPY_CLIENT_ID")
        SPOTIPY_CLIENT_SECRET = config.get("SPOTIPY_CLIENT_SECRET")
        GEMINI_API_KEYS = [key.strip() for key in config.get('GEMINI_API_KEYS', []) if key.strip()]
        OWNER_ID = config.get("OWNER_ID")

except (FileNotFoundError, json.JSONDecodeError) as e:
    print(f"BŁĄD KRYTYCZNY: Nie udało się wczytać pliku config.json: {e}")
    exit()

if not BOT_TOKEN:
    print("BŁĄD KRYTYCZNY: Nie zdefiniowano BOT_TOKEN w config.json! Zakończono działanie.")
    exit()

server_configs = {}

# --- Konfiguracja AI Gemini ---
import google.generativeai as genai

gemini_model = None
if GEMINI_API_KEYS:
    for key in GEMINI_API_KEYS:
        try:
            genai.configure(api_key=key)
            gemini_model = genai.GenerativeModel('models/gemini-pro-latest')
            print(f"Model Gemini AI został pomyślnie skonfigurowany z kluczem: {key[:5]}...{key[-5:]}")
            break
        except Exception as e:
            print(f"Błąd podczas próby konfiguracji Gemini AI z kluczem {key[:5]}...{key[-5:]}: {e}")
    
    if not gemini_model:
        print("Ostrzeżenie: Żaden z podanych kluczy GEMINI_API_KEYS w config.json nie pozwolił na pomyślną konfigurację. Funkcje AI nie będą działać.")
else:
    print("Ostrzeżenie: Nie znaleziono kluczy GEMINI_API_KEYS w pliku config.json. Funkcje AI nie będą działać.")


# --- Konfiguracja Filtra Słów ---
FORBIDDEN_WORDS = []
ANTI_SPAM_CONFIG = {}
user_message_timestamps = {}

try:
    with open(os.path.join(DATA_DIR, 'config.json'), 'r', encoding='utf-8') as f:
        config = json.load(f)
        
        # Konfiguracja FORBIDDEN_WORDS
        words = config.get('FORBIDDEN_WORDS', [])
        if isinstance(words, list):
            FORBIDDEN_WORDS = [str(word).lower() for word in words]
            
        # Konfiguracja Anti-Spam zagnieżdżona w LIVE_MODERATION
        live_moderation_config = config.get("LIVE_MODERATION", {})
        if live_moderation_config.get("ENABLED", False):
            ANTI_SPAM_CONFIG = live_moderation_config.get("ANTI_SPAM", {})

except (FileNotFoundError, json.JSONDecodeError) as e:
    print(f"Ostrzeżenie: Nie udało się załadować konfiguracji z config.json: {e}")
    pass


# --- Konfiguracja Bota ---
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.presences = True

class MyBot(commands.Bot):
    async def close(self):
        print("Zamykanie bota i wykonywanie procedur czyszczących...")
        if self.is_ready():
            await on_shutdown()
        await super().close()

bot = MyBot(command_prefix="!", intents=intents, owner_id=OWNER_ID)
bot.remove_command('help')

# --- Konfiguracja Spotify ---
try:
    if SPOTIPY_CLIENT_ID and SPOTIPY_CLIENT_SECRET:
        spotify = spotipy.Spotify(auth_manager=SpotifyClientCredentials(client_id=SPOTIPY_CLIENT_ID,
                                                                         client_secret=SPOTIPY_CLIENT_SECRET))
    else:
        spotify = None
        print("Ostrzeżenie: Nie znaleziono kluczy Spotify API. Odtwarzanie z linków Spotify nie będzie działać.")
except Exception as e:
    spotify = None
    print(f"Błąd podczas inicjalizacji Spotify: {e}")

# --- Zmienne globalne ---
music_player = None
game_instance = None
scores = {}
current_station_name = None
current_ai_personality = None # Nowa zmienna dla osobowości AI

# ID są teraz ładowane z pliku config.json
temp_channels = {} # Zmieniono z set na dict do przechowywania par {voice_id: text_id}


class TempChannelView(discord.ui.View):
    def __init__(self, voice_channel, text_channel):
        super().__init__(timeout=None) # Panel nie wygaśnie
        self.voice_channel = voice_channel
        self.text_channel = text_channel

    @discord.ui.button(label="Zmień Nazwę", style=discord.ButtonStyle.primary, emoji="✏️")
    async def change_name(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Podaj nową nazwę dla kanału głosowego:", ephemeral=True)
        try:
            def check(m):
                return m.author == interaction.user and m.channel == self.text_channel

            msg = await interaction.client.wait_for('message', check=check, timeout=60.0)
            new_name = msg.content
            try:
                await self.voice_channel.edit(name=new_name)
                await interaction.followup.send(f"Zmieniono nazwę kanału na: **{new_name}**", ephemeral=True)
                await msg.delete()
            except Exception as e:
                error_message = f"Błąd krytyczny przy zmianie nazwy kanału: {type(e).__name__}: {e}"
                print(error_message)
                try:
                    await interaction.followup.send(f"Wystąpił nieoczekiwany błąd. Został on zgłoszony właścicielowi bota.", ephemeral=True)
                    owner = await interaction.client.fetch_user(interaction.client.owner_id)
                    await owner.send(f'''## ⚠️ Błąd w `change_name`
**Serwer:** {interaction.guild.name}
**Kanał:** {self.voice_channel.name}
**Użytkownik:** {interaction.user.mention}
**Treść błędu:**
```
{error_message}
```''')
                except Exception as dm_e:
                    print(f"BŁĄD KRYTYCZNY: Nie udało się nawet wysłać DM do właściciela. Błąd DM: {dm_e}")
        except asyncio.TimeoutError:
            await interaction.followup.send("Przekroczono czas na odpowiedź.", ephemeral=True)

    @discord.ui.button(label="Ustaw Limit", style=discord.ButtonStyle.secondary, emoji="👥")
    async def set_limit(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("Podaj nowy limit użytkowników (wpisz 0, aby usunąć limit):", ephemeral=True)
        try:
            def check(m):
                return m.author == interaction.user and m.channel == self.text_channel and (m.content.isdigit() or m.content == '0')

            msg = await interaction.client.wait_for('message', check=check, timeout=60.0)
            limit = int(msg.content)
            await self.voice_channel.edit(user_limit=limit)
            limit_str = f"{limit} użytkowników" if limit > 0 else "Brak limitu"
            await interaction.followup.send(f"Ustawiono limit na: **{limit_str}**", ephemeral=True)
            await msg.delete()
        except asyncio.TimeoutError:
            await interaction.followup.send("Przekroczono czas na odpowiedź.", ephemeral=True)
        except ValueError:
            await interaction.followup.send("Podana wartość nie jest prawidłową liczbą.", ephemeral=True)


# --- Funkcja pomocnicza do zarządzania połączeniem głosowym ---
async def get_voice_client(ctx):
    if not ctx.author.voice:
        await ctx.send("Musisz być na kanale głosowym, aby użyć tej komendy!")
        return None

    channel = ctx.author.voice.channel
    if ctx.voice_client is None:
        try:
            return await channel.connect(timeout=60.0, reconnect=True)
        except Exception as e:
            await ctx.send(f"Wystąpił nieoczekiwany błąd podczas łączenia: {e}")
            return None
    elif ctx.voice_client.channel != channel:
        await ctx.voice_client.move_to(channel)

    return ctx.voice_client

# --- Klasa do obsługi muzyki ---
class MusicPlayer:
    def __init__(self, ctx):
        self.ctx = ctx
        self.voice_client = ctx.voice_client
        self.queue = asyncio.Queue()
        self.current_track = None
        self.play_next_song = asyncio.Event()
        self.bot = ctx.bot

    def _extract_info(self, url):
        ydl_opts = {
            'format': 'bestaudio/best',
            'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}],
            'outtmpl': os.path.join(DATA_DIR, './%(id)s.%(ext)s'),
            'default_search': 'ytsearch',
            'noplaylist': True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if 'entries' in info:
                info = info['entries'][0]
            return info

    async def play_song(self, url):
        try:
            info = await self.bot.loop.run_in_executor(None, self._extract_info, url)
            stream_url = info['url']
            self.current_track = info['title']
            ffmpeg_options = {
                'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
                'options': '-vn -loglevel panic'
            }
            self.voice_client.play(discord.FFmpegPCMAudio(stream_url, **ffmpeg_options), after=lambda e: self.play_next_song.set())

            activity = discord.Activity(type=discord.ActivityType.listening, name=f"{self.current_track}")
            await self.bot.change_presence(activity=activity)
            await self.ctx.send(f"▶️ Odtwarzam: **{self.current_track}**")
        except Exception as e:
            await self.ctx.send(f"Wystąpił błąd podczas odtwarzania: {e}")
            self.play_next_song.set()

    async def player_loop(self):
        await self.bot.wait_until_ready()
        while not self.bot.is_closed():
            self.play_next_song.clear()
            try:
                async with asyncio.timeout(300):
                    next_song = await self.queue.get()
                await self.play_song(next_song)
                await self.play_next_song.wait()
            except asyncio.TimeoutError:
                await self.ctx.send("Bot bezczynny zbyt długo. Odłączam się.")
                if self.voice_client:
                    await self.voice_client.disconnect()
                await self.bot.change_presence(activity=None)
                global music_player
                music_player = None
                return
            except Exception as e:
                print(f"Błąd w pętli odtwarzacza: {e}")
                continue

# --- Komendy muzyczne ---

@bot.command(name='?help')
@commands.has_permissions(manage_messages=True)
async def help_command(ctx):
    """Wyświetla tę wiadomość pomocy."""
    embed = discord.Embed(
        title="Centrum Pomocy Bota",
        description="Oto lista wszystkich dostępnych komend, podzielona na kategorie.",
        color=discord.Color.blurple()
    )

    embed.add_field(
        name="👑 Moderacja",
        value="`!kara <strefa> <user> <TAK/NIE> <powód>` - Tworzy kartę kary dla użytkownika.\n`!clear <liczba>` - Czyści określoną liczbę wiadomości.\n`!lock-roz <czas> <user> [powód]` - Tymczasowo blokuje użytkownika na kanale rozmów.\n`!unlock-roz <user>` - Ręcznie odblokowuje użytkownika na kanale rozmów.",
        inline=False
    )

    embed.add_field(
        name="🎵 Muzyka",
        value="`!play <piosenka/link>` - Odtwarza piosenkę lub dodaje do kolejki.\n`!stop` - Zatrzymuje odtwarzanie i czyści kolejkę.\n`!skip` - Pomija aktualnie odtwarzany utwór.\n`!queue` - Wyświetla kolejkę piosenek.\n`!nowplaying` - Pokazuje, co jest aktualnie grane.",
        inline=False
    )

    embed.add_field(
        name="🎲 Rozrywka i Inne",
        value="`!zgaduj` - Rozpoczyna grę w 'Jaka to melodia?'.\n`!ranking` - Wyświetla ranking gry w zgadywanie.\n`!sound <nazwa>` - Odtwarza dźwięk z soundboardu.\n`!listsounds` - Pokazuje listę dostępnych dźwięków.\n`!user_info [użytkownik]` - Wyświetla informacje o użytkowniku.\n`!shutdown` - Wyłącza bota (Tylko właściciel).",
        inline=False
    )

    embed.add_field(
        name="ℹ️ O Autorze",
        value="Zajmuję się tworzeniem zaawansowanych botów na Discorda. Więcej informacji znajdziesz na [mojej stronie](https://botydiscord.unaux.com)!",
        inline=False
    )

    embed.set_footer(text=f"Bot na serwerze {ctx.guild.name}")
    await ctx.send(embed=embed)

@bot.command(name='admin-help', aliases=['mod-help', 'pomoc-admin'])
@commands.has_permissions(manage_messages=True)
async def admin_help_command(ctx):
    """Wyświetla pomoc dla administratorów i moderatorów."""
    embed = discord.Embed(
        title="Panel Pomocy dla Administratorów i Moderatorów",
        description="Oto lista komend administracyjnych i moderacyjnych, dostępnych tylko dla osób z odpowiednimi uprawnieniami.",
        color=discord.Color.red()
    )

    embed.add_field(
        name="👑 Moderacja i Kary",
        value=(
            "`!kara <strefa> <user> <TAK/NIE> <powód>` - Tworzy kartę kary dla użytkownika.\n"
            "`!historia <user>` - Wyświetla historię moderacyjną użytkownika.\n"
            "`!clear <liczba>` - Czyści określoną liczbę wiadomości z kanału.\n"
            "`!lock-roz <czas> <user> [powód]` - Tymczasowo blokuje użytkownika na kanale rozmów.\n"
            "`!unlock-roz <user>` - Ręcznie odblokowuje użytkownika na kanale rozmów."
        ),
        inline=False
    )

    embed.add_field(
        name="🛠️ Narzędzia Serwerowe",
        value=(
            "`!stwórz-ogloszenie` - Interaktywny kreator wiadomości embed (ogłoszeń).\n"
            "`!say-r <ID_kanału> <treść>` - Wysyła wiadomość na podany kanał w imieniu bota (tylko właściciel).\n"
            "`!summarize [liczba]` - Streszcza ostatnie wiadomości na kanale przy użyciu AI.\n"
            "`!do <komenda>` - Wykonuje inną komendę bota (tylko właściciel)."
        ),
        inline=False
    )

    embed.add_field(
        name="🤖 Zarządzanie AI",
        value=(
            "`!setpersonality <prompt>` - Ustawia niestandardową osobowość dla AI (tylko właściciel).\n"
            "`!resetpersonality` - Resetuje osobowość AI do domyślnej (tylko właściciel)."
        ),
        inline=False
    )

    embed.set_footer(text=f"Wymagane uprawnienia: Zarządzanie wiadomościami lub wyższe.")
    await ctx.send(embed=embed)

@bot.command(name='summarize')
@commands.has_permissions(manage_messages=True)
async def summarize(ctx, limit: int = 100):
    """Summarizes the last N messages in the channel using Gemini AI."""
    if not gemini_model:
        await ctx.send("Funkcja podsumowania jest niedostępna, ponieważ klucz API Gemini nie jest skonfigurowany.")
        return

    if limit <= 0 or limit > 1000:
        await ctx.send("Liczba wiadomości do podsumowania musi być między 1 a 1000.")
        return

    await ctx.send(f"Proszę czekać, analizuję ostatnie {limit} wiadomości...")
    async with ctx.typing():
        try:
            messages = []
            async for msg in ctx.channel.history(limit=limit):
                if not msg.author.bot:
                    messages.append(f"{msg.author.name}: {msg.content}")
            
            if not messages:
                await ctx.send("Nie znaleziono wiadomości do podsumowania.")
                return

            # Reverse the messages to have them in chronological order
            messages.reverse()
            chat_history = "\n".join(messages)

            prompt = f"Jesteś asystentem AI. Twoim zadaniem jest przeanalizowanie poniższej historii czatu i stworzenie zwięzłego podsumowania najważniejszych tematów i dyskusji. Podsumowanie powinno być w języku polskim.\n\nOto historia czatu:\n\n---\n{chat_history}\n---\n\nPodsumowanie:"

            response = await asyncio.to_thread(gemini_model.generate_content, prompt)

            if response and response.text:
                embed = discord.Embed(
                    title=f"Podsumowanie ostatnich {len(messages)} wiadomości",
                    description=response.text,
                    color=discord.Color.blue()
                )
                await ctx.send(embed=embed)
            else:
                await ctx.send("Nie udało się wygenerować podsumowania.")

        except Exception as e:
            await ctx.send(f"Wystąpił błąd podczas generowania podsumowania: {e}")
            print(f"Błąd w komendzie summarize: {e}")

@bot.command(aliases=['p'])
async def play(ctx, *, search: str):
    global music_player
    vc = await get_voice_client(ctx)
    if not vc:
        return

    if not music_player:
        music_player = MusicPlayer(ctx)
        bot.loop.create_task(music_player.player_loop())

    spotify_url_pattern = r'https://open.spotify.com/(track|playlist|album)/([a-zA-Z0-9]+)'
    match = re.match(spotify_url_pattern, search)

    if match and spotify:
        url_type = match.group(1)
        spotify_id = match.group(2)
        try:
            await ctx.send(f"Analizuję link ze Spotify...")
            if url_type == 'track':
                track = spotify.track(spotify_id)
                song_query = f"{track['artists'][0]['name']} - {track['name']}"
                await music_player.queue.put(song_query)
                await ctx.send(f"Dodano do kolejki ze Spotify: **{song_query}**")
            elif url_type == 'playlist':
                results = spotify.playlist_items(spotify_id)
                tracks = results['items']
                for item in tracks:
                    track = item['track']
                    if track:
                        song_query = f"{track['artists'][0]['name']} - {track['name']}"
                        await music_player.queue.put(song_query)
                await ctx.send(f"Dodano **{len(tracks)}** utworów z playlisty Spotify do kolejki.")
            elif url_type == 'album':
                results = spotify.album_tracks(spotify_id)
                tracks = results['items']
                for track in tracks:
                    if track:
                        song_query = f"{track['artists'][0]['name']} - {track['name']}"
                        await music_player.queue.put(song_query)
                await ctx.send(f"Dodano **{len(tracks)}** utworów z albumu Spotify do kolejki.")
        except Exception as e:
            await ctx.send(f"Nie udało się przetworzyć linku ze Spotify. Błąd: {e}")
    elif match and not spotify:
        await ctx.send("Wykryto link Spotify, ale klucze API nie są skonfigurowane. Ta funkcja jest wyłączona.")
    else:
        await music_player.queue.put(search)
        await ctx.send(f"Dodano do kolejki: **{search}**")

@bot.command(aliases=['s'])
async def skip(ctx):
    if music_player and music_player.voice_client and music_player.voice_client.is_playing():
        music_player.voice_client.stop()
        await ctx.send("Pominięto piosenkę.")

@bot.command(aliases=['q'])
async def queue(ctx):
    if music_player and not music_player.queue.empty():
        queue_list = list(music_player.queue._queue)
        embed = discord.Embed(title="Kolejka piosenek", color=discord.Color.blue())
        for i, song in enumerate(queue_list, 1):
            embed.add_field(name=f"#{i}", value=song, inline=False)
        await ctx.send(embed=embed)
    else:
        await ctx.send("Kolejka jest pusta.")

@bot.command()
async def stop(ctx):
    global music_player
    if music_player and music_player.voice_client:
        music_player.queue = asyncio.Queue()
        music_player.voice_client.stop()
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
    music_player = None
    await bot.change_presence(activity=None)
    await ctx.send("Zatrzymałem muzykę, wyczyściłem kolejkę i odłączyłem się.")

@bot.command()
@commands.is_owner()
async def shutdown(ctx):
    """Gracefully shuts down the bot."""
    await ctx.send("Wyłączam bota...")
    await bot.close()

@bot.command()
@commands.has_permissions(manage_messages=True)
async def clear(ctx, amount: int):
    """Clears a specified number of messages from the channel."""
    if amount <= 0:
        await ctx.send("Liczba wiadomości do usunięcia musi być dodatnia.", delete_after=5)
        return
    
    deleted = await ctx.channel.purge(limit=amount + 1)
    await ctx.send(f"Usunięto {len(deleted) - 1} wiadomości.", delete_after=5)

@clear.error
async def clear_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("Musisz podać liczbę wiadomości do usunięcia. Np. `!clear 10`")
    elif isinstance(error, commands.MissingPermissions):
        await ctx.send("Nie masz uprawnień do usuwania wiadomości.")
    elif isinstance(error, commands.BadArgument):
        await ctx.send("Podana wartość musi być liczbą.")
    else:
        print(f"Wystąpił błąd w komendzie clear: {error}")
        await ctx.send("Wystąpił nieoczekiwany błąd.")


@bot.command(name='say-r')
@commands.is_owner()
async def say_command(ctx, channel_id: int, *, message: str):
    """Wysyła wiadomość na podany kanał w imieniu bota. Tylko dla właściciela."""
    try:
        await ctx.message.delete()
    except discord.Forbidden:
        print(f"Brak uprawnień do usunięcia wiadomości z komendą 'say' na kanale {ctx.channel.name}.")
    except discord.NotFound:
        pass # Wiadomość mogła zostać już usunięta

    target_channel = bot.get_channel(channel_id)
    if target_channel:
        try:
            await target_channel.send(message)
        except discord.Forbidden:
            await ctx.author.send(f"Nie mam uprawnień do wysyłania wiadomości na kanale o ID: {channel_id}", delete_after=15)
    else:
        await ctx.author.send(f"Nie mogłem znaleźć kanału o ID: {channel_id}", delete_after=15)

@say_command.error
async def say_command_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("Błąd: Złe użycie komendy. Poprawne użycie: `!say <ID_kanału> <treść wiadomości>`", delete_after=10)
    elif isinstance(error, commands.NotOwner):
        # Cicha obsługa błędu, aby zwykli użytkownicy nie wiedzieli o komendzie
        try:
            await ctx.message.delete()
        except (discord.Forbidden, discord.NotFound):
            pass
    elif isinstance(error, commands.BadArgument):
        await ctx.send("Błąd: ID kanału musi być liczbą.", delete_after=10)
    else:
        print(f"Wystąpił błąd w komendzie say: {error}")
        await ctx.send("Wystąpił nieoczekiwany błąd.", delete_after=10)


@bot.command(name='setpersonality')
@commands.is_owner()
async def set_personality(ctx, *, personality_prompt: str):
    """Ustawia niestandardową osobowość dla AI."""
    global current_ai_personality
    current_ai_personality = personality_prompt
    try:
        await ctx.message.delete()
    except (discord.Forbidden, discord.NotFound):
        pass
    await ctx.send(f"Ustawiono nową osobowość AI: `{personality_prompt}`", delete_after=15)

@bot.command(name='resetpersonality')
@commands.is_owner()
async def reset_personality(ctx):
    """Resetuje osobowość AI do domyślnej."""
    global current_ai_personality
    current_ai_personality = None
    try:
        await ctx.message.delete()
    except (discord.Forbidden, discord.NotFound):
        pass
    await ctx.send("Zresetowano osobowość AI do domyślnej.", delete_after=10)


@bot.command(name='do')
@commands.is_owner()
async def do_command(ctx, *, command_string: str):
    """
    Wykonuje inną komendę bota, tak jakby została wpisana przez użytkownika.
    Użycie: !do <komenda_do_wykonania>
    """
    try:
        await ctx.message.delete() # Usuń wiadomość z komendą !do
    except (discord.Forbidden, discord.NotFound):
        pass

    # Parse the command string
    prefix = bot.command_prefix
    if not command_string.startswith(prefix):
        await ctx.author.send(f"Komenda musi zaczynać się od prefiksu bota (`{prefix}`).", delete_after=10)
        return

    command_name_and_args = command_string[len(prefix):].strip()
    command_name = command_name_and_args.split(' ')[0]
    args_string = command_name_and_args[len(command_name):].strip()

    command = bot.get_command(command_name)

    if command:
        try:
            # Create a new context for the command
            # This is the cleanest way to invoke a command programmatically
            new_ctx = await bot.get_context(ctx.message) # Use original message as template
            new_ctx.message.content = command_string # Override content
            
            # Invoke the command
            await bot.invoke(new_ctx)
            await ctx.author.send(f"Wykonano komendę: `{command_string}`", delete_after=10)
        except Exception as e:
            await ctx.author.send(f"Błąd podczas wykonywania komendy `{command_string}`: {e}", delete_after=15)
    else:
        await ctx.author.send(f"Nie znaleziono komendy: `{command_name}`", delete_after=10)

@do_command.error
async def do_command_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.author.send("Błąd: Musisz podać komendę do wykonania. Użycie: `!do <komenda>`", delete_after=10)
    elif isinstance(error, commands.NotOwner):
        try:
            await ctx.message.delete()
        except (discord.Forbidden, discord.NotFound):
            pass
    else:
        print(f"Wystąpił błąd w komendzie !do: {error}")
        await ctx.author.send("Wystąpił nieoczekiwany błąd podczas wykonywania komendy.", delete_after=10)



LOCK_FILE = os.path.join(DATA_DIR, "channel_locks.json")
_lock_file_lock = asyncio.Lock()

async def _read_locks():
    async with _lock_file_lock:
        try:
            with open(LOCK_FILE, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            return []

async def _write_locks(data):
    async with _lock_file_lock:
        with open(LOCK_FILE, 'w') as f:
            json.dump(data, f, indent=4)

KARA_HISTORY_FILE = os.path.join(DATA_DIR, "kara_history.json")
_kara_history_lock = asyncio.Lock()

async def _read_kara_history():
    async with _kara_history_lock:
        try:
            with open(KARA_HISTORY_FILE, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            return {}

async def _write_kara_history(data):
    async with _kara_history_lock:
        with open(KARA_HISTORY_FILE, 'w') as f:
            json.dump(data, f, indent=4)

async def _handle_unlock(bot, user_id: int, channel_id: int, reason: str):
    guild = bot.guilds[0] # Assuming the bot is only in one server

    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except (discord.NotFound, discord.Forbidden):
            print(f"Błąd w _handle_unlock: Nie znaleziono kanału o ID {channel_id} lub brak dostępu.")
            return False, None

    member = None
    if guild:
        try:
            member = await guild.fetch_member(user_id)
        except discord.NotFound:
            print(f"Błąd w _handle_unlock: Nie znaleziono użytkownika o ID {user_id} w gildii.")
        except discord.Forbidden:
            print(f"Błąd w _handle_unlock: Brak uprawnień do pobrania użytkownika o ID {user_id}.")

    if not channel or not member:
        return False, channel # Return channel, even if member is not found

    try:
        await channel.set_permissions(member, overwrite=None, reason=reason)
        return True, channel
    except Exception as e:
        print(f"Błąd podczas odblokowywania {user_id}: {e}")
        return False, channel

async def _schedule_unlock(bot, user_id: int, channel_id: int, unlock_at: float):
    delay = unlock_at - datetime.utcnow().timestamp()
    if delay > 0:
        await asyncio.sleep(delay)
    
    unlocked, channel = await _handle_unlock(bot, user_id, channel_id, "Automatyczne odblokowanie po upływie czasu.")
    
    if unlocked and channel:
        mod_log_channel_id = config.get("MOD_LOG_CHANNEL_ID")

        member = bot.get_user(user_id)
        embed = discord.Embed(title="🔓 Użytkownik Automatycznie Odblokowany", color=discord.Color.green(), timestamp=datetime.utcnow())
        if member:
            embed.set_thumbnail(url=member.display_avatar.url)
            embed.add_field(name="Użytkownik", value=member.mention, inline=False)
        else:
            embed.add_field(name="Użytkownik", value=f"ID: {user_id}", inline=False)
        await channel.send(embed=embed)

        # Send log to specific channel
        if mod_log_channel_id:
            log_channel = bot.get_channel(mod_log_channel_id)
            if log_channel:
                log_embed = discord.Embed(
                    title="📝 Zapis z Dziennika Aktywności",
                    description=f"Użytkownik {member.mention if member else user_id} został **automatycznie** odblokowany na kanale {channel.mention}.",
                    color=discord.Color.dark_green(),
                    timestamp=datetime.utcnow()
                )
                log_embed.set_footer(text=f"ID Użytkownika: {user_id}")
                await log_channel.send(embed=log_embed)

    locks = await _read_locks()
    locks = [lock for lock in locks if not (lock['user_id'] == user_id and lock['channel_id'] == channel_id)]
    await _write_locks(locks)

async def check_persistent_locks(bot):
    locks = await _read_locks()
    if not locks:
        return

    print(f"Znaleziono {len(locks)} zapisanych blokad. Przetwarzanie...")
    for lock in locks:
        bot.loop.create_task(
            _schedule_unlock(bot, lock['user_id'], lock['channel_id'], lock['unlock_at'])
        )

@bot.command(name="lock-roz")
@commands.has_permissions(manage_channels=True)
async def lock_roz(ctx, time_str: str, member: discord.Member, *, reason: str = "Brak powodu"):
    channel_id = config.get("LOCK_ROZ_CHANNEL_ID")
    if not channel_id:
        return await ctx.send("Brak zdefiniowanego kanału do blokowania w konfiguracji serwera.")

    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except discord.NotFound:
            return await ctx.send(f"Nie znaleziono kanału docelowego o ID `{channel_id}`. Sprawdź konfigurację serwera.")
        except discord.Forbidden:
            return await ctx.send(f"Nie mam uprawnień do odczytania kanału o ID `{channel_id}`.")

    if not channel:
        return await ctx.send("Nie znaleziono kanału docelowego. Sprawdź konfigurację serwera.")

    duration_seconds = 0
    unit = time_str[-1].lower()
    try:
        value = int(time_str[:-1])
        if unit == 's': duration_seconds = value
        elif unit == 'm': duration_seconds = value * 60
        elif unit == 'h': duration_seconds = value * 3600
        elif unit == 'd': duration_seconds = value * 86400
        else: raise ValueError()
    except (ValueError, TypeError):
        return await ctx.send("Nieprawidłowy format czasu. Użyj np. `10s`, `5m`, `1h`, `2d`.")

    if duration_seconds <= 0:
        return await ctx.send("Czas musi być dodatni.")

    unlock_at = datetime.utcnow().timestamp() + duration_seconds

    try:
        await channel.set_permissions(member, send_messages=False, reason=f"Zablokowany przez {ctx.author.name}: {reason}")
        # Send log to specific channel
        mod_log_channel_id = config.get("MOD_LOG_CHANNEL_ID")
        if mod_log_channel_id:
            log_channel = bot.get_channel(mod_log_channel_id)
            if log_channel:
                log_embed = discord.Embed(
                    title="📝 Zapis z Dziennika Aktywności",
                    description=f"Użytkownik {member.mention} został wyciszony na kanale {channel.mention} na czas `{time_str}`.",
                    color=discord.Color.blue(),
                    timestamp=datetime.utcnow()
                )
                log_embed.add_field(name="Moderator", value=ctx.author.mention, inline=True)
                log_embed.add_field(name="Powód", value=reason, inline=True)
                log_embed.set_footer(text=f"ID Użytkownika: {member.id}")
                await log_channel.send(embed=log_embed)

    except Exception as e:
        return await ctx.send(f":x: Wystąpił błąd podczas blokowania użytkownika: {e}")

    locks = await _read_locks()
    locks = [lock for lock in locks if not (lock['user_id'] == member.id and lock['channel_id'] == channel_id)]
    locks.append({"user_id": member.id, "channel_id": channel_id, "unlock_at": unlock_at})
    await _write_locks(locks)

    bot.loop.create_task(_schedule_unlock(bot, member.id, channel_id, unlock_at))

    lock_embed = discord.Embed(title="🔒 Użytkownik Zablokowany na Kanale", color=discord.Color.red(), timestamp=datetime.utcnow())
    lock_embed.set_thumbnail(url=member.display_avatar.url)
    lock_embed.add_field(name="Użytkownik", value=member.mention, inline=False)
    lock_embed.add_field(name="Moderator", value=ctx.author.mention, inline=False)
    lock_embed.add_field(name="Czas", value=time_str, inline=False)
    lock_embed.add_field(name="Powód", value=reason, inline=False)
    lock_embed.set_footer(text=f"ID Użytkownika: {member.id}")
    await ctx.send(embed=lock_embed)

    try:
        dm_embed = discord.Embed(title=f" zostałeś tymczasowo zablokowany na kanale!", color=discord.Color.orange(), timestamp=datetime.utcnow())
        dm_embed.set_thumbnail(url=member.display_avatar.url)
        dm_embed.add_field(name="Serwer", value=ctx.guild.name, inline=False)
        dm_embed.add_field(name="Kanał", value=channel.mention, inline=False)
        dm_embed.add_field(name="Zablokowany przez", value=ctx.author.mention, inline=False)
        dm_embed.add_field(name="Czas", value=time_str, inline=False)
        dm_embed.add_field(name="Powód", value=reason, inline=False)
        await member.send(embed=dm_embed)
    except discord.Forbidden:
        await ctx.send("(Nie udało się wysłać wiadomości DM do użytkownika.)", delete_after=10)

@bot.command(name="unlock-roz")
@commands.has_permissions(manage_channels=True)
async def unlock_roz(ctx, member: discord.Member, *, reason: str = "Brak powodu"):
    channel_id = config.get("LOCK_ROZ_CHANNEL_ID")
    if not channel_id:
        return await ctx.send("Brak zdefiniowanego kanału do odblokowywania w konfiguracji serwera.")

    unlocked, channel = await _handle_unlock(bot, member.id, channel_id, f"Ręcznie odblokowany przez {ctx.author.name}: {reason}")
    
    if not channel:
        return await ctx.send("Nie znaleziono kanału docelowego. Sprawdź konfigurację serwera.")

    if unlocked:
        locks = await _read_locks()
        locks = [lock for lock in locks if not (lock['user_id'] == member.id and lock['channel_id'] == channel_id)]
        await _write_locks(locks)
        
        embed = discord.Embed(title="🔓 Użytkownik Ręcznie Odblokowany", color=discord.Color.green(), timestamp=datetime.utcnow())
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Użytkownik", value=member.mention, inline=False)
        embed.add_field(name="Moderator", value=ctx.author.mention, inline=False)
        await ctx.send(embed=embed)

        # Send log to specific channel
        mod_log_channel_id = config.get("MOD_LOG_CHANNEL_ID")
        if mod_log_channel_id:
            log_channel = bot.get_channel(mod_log_channel_id)
            if log_channel:
                log_embed = discord.Embed(
                    title="📝 Zapis z Dziennika Aktywności",
                    description=f"Użytkownik {member.mention} został **ręcznie** odblokowany na kanale {channel.mention}.",
                    color=discord.Color.dark_green(),
                    timestamp=datetime.utcnow()
                )
                log_embed.add_field(name="Odblokowany przez", value=ctx.author.mention, inline=True)
                log_embed.add_field(name="Powód", value=reason, inline=True)
                log_embed.set_footer(text=f"ID Użytkownika: {member.id}")
                await log_channel.send(embed=log_embed)

    else:
        await ctx.send(f"Nie udało się odblokować użytkownika {member.mention}. Sprawdź konsolę bota.")

@lock_roz.error
@unlock_roz.error
async def lock_unlock_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("Brak argumentu. Użycie: `!lock-roz <czas> <użytkownik>` lub `!unlock-roz <użytkownik>`")
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send("Nie znaleziono takiego użytkownika.")
    elif isinstance(error, commands.MissingPermissions):
        await ctx.send("Nie masz uprawnień do zarządzania kanałami.")
    else:
        print(f"Wystąpił błąd w komendzie lock/unlock: {error}")
        await ctx.send("Wystąpił nieoczekiwany błąd.")

@bot.command(name="kara")
@commands.has_permissions(manage_messages=True)
async def kara(ctx, strefa: str, member: discord.Member, odwolanie: str, mute_duration: str = None, *, reason: str):
    """Tworzy kartę kary dla użytkownika."""
    punishment_roles = config.get("PUNISHMENT_ROLES", {})
    mod_log_channel_id = config.get("MOD_LOG_CHANNEL_ID")

    strefa = strefa.lower()
    odwolanie = odwolanie.upper()

    # Define punishment progression (linear sequence of levels)
    PUNISHMENT_PROGRESSION = [
        {"name": "Zero", "strefa_base": "zero", "role_id": punishment_roles.get("YOUR_ZERO_ROLE_ID"), "action": "assign_role"},
        {"name": "Zielona 1", "strefa_base": "zielona", "role_id": punishment_roles.get("YOUR_GREEN_ROLE_ID_1"), "action": "assign_role"},
        {"name": "Zielona 2", "strefa_base": "zielona", "role_id": punishment_roles.get("YOUR_GREEN_ROLE_ID_2"), "action": "assign_role"},
        {"name": "Zielona 3", "strefa_base": "zielona", "role_id": punishment_roles.get("YOUR_GREEN_ROLE_ID_3"), "action": "assign_role"},
        {"name": "Żółta 1", "strefa_base": "żółta", "role_id": punishment_roles.get("YOUR_YELLOW_ROLE_ID_1"), "action": "assign_role"},
        {"name": "Żółta 2", "strefa_base": "żółta", "role_id": punishment_roles.get("YOUR_YELLOW_ROLE_ID_2"), "action": "assign_role"},
        {"name": "Żółta 3", "strefa_base": "żółta", "role_id": punishment_roles.get("YOUR_YELLOW_ROLE_ID_3"), "action": "assign_role"},
        {"name": "Czerwona 1", "strefa_base": "czerwona", "role_id": punishment_roles.get("YOUR_RED_ROLE_ID_1"), "action": "assign_role"},
        {"name": "Czerwona 2", "strefa_base": "czerwona", "role_id": punishment_roles.get("YOUR_RED_ROLE_ID_2"), "action": "assign_role"},
        {"name": "Czarna", "strefa_base": "czarna", "role_id": None, "action": "ban"},
    ]

    ZONES = {
        "zero": {"emoji": "0️⃣", "color": discord.Color.light_grey(), "name": "Strefa Zero"},
        "zielona": {"emoji": "🟢", "color": discord.Color.green(), "name": "Strefa Zielona"},
        "żółta": {"emoji": "🟡", "color": discord.Color.gold(), "name": "Strefa Żółta"},
        "czerwona": {"emoji": "🔴", "color": discord.Color.red(), "name": "Strefa Czerwona"},
        "czarna": {"emoji": "⚫", "color": discord.Color.dark_grey(), "name": "Strefa Czarna"}
    }

    if strefa not in ZONES:
        return await ctx.send(f"Nieprawidłowa strefa. Dostępne strefy: {', '.join(ZONES.keys())}")

    if odwolanie not in ["TAK", "NIE"]:
        return await ctx.send("Możliwość odwołania musi być `TAK` lub `NIE`.")

    if strefa == "zero":
        await ctx.send("Kary ze strefy `zero` nie są publicznie ogłaszane. Pamiętaj o pouczeniu słownym.", delete_after=10)
        return

    zone_info = ZONES[strefa]

    embed_title = "📝 Ostrzeżenie"
    embed_description = ""
    is_banned = False
    escalation_message = ""

    # --- Read and Update Kara History ---
    kara_history = await _read_kara_history()
    user_id_str = str(member.id)
    user_history = kara_history.get(user_id_str, {"punishments": [], "current_role_id": None})

    if strefa != "zero" and strefa != "czarna": # "zero" is verbal, "czarna" is ban, not counted for progression
        # Add new punishment to the list
        new_punishment = {
            "strefa": strefa,
            "reason": reason,
            "moderator": ctx.author.name,
            "date": datetime.utcnow().isoformat(),
            "mute_duration": mute_duration
        }
        user_history["punishments"].append(new_punishment)

        # --- Mute logic ---
        if mute_duration:
            duration_seconds = 0
            unit = mute_duration[-1].lower()
            try:
                value = int(mute_duration[:-1])
                if unit == 's': duration_seconds = value
                elif unit == 'm': duration_seconds = value * 60
                elif unit == 'h': duration_seconds = value * 3600
                elif unit == 'd': duration_seconds = value * 86400
                else: raise ValueError()
            except (ValueError, TypeError):
                await ctx.send("Nieprawidłowy format czasu wyciszenia. Użyj np. `10s`, `5m`, `1h`, `2d`.", delete_after=10)
                return

            if duration_seconds > 0:
                try:
                    await member.timeout(timedelta(seconds=duration_seconds), reason=f"Kara: {reason}")
                    await ctx.send(f"Wyciszono {member.mention} na {mute_duration}.", delete_after=10)
                except discord.Forbidden:
                    await ctx.send("Nie mam uprawnień do wyciszania tego użytkownika.", delete_after=10)
                except Exception as e:
                    await ctx.send(f"Wystąpił błąd podczas wyciszania: {e}", delete_after=10)

        # --- Logika Progresywnego Karania ---
        strefa_count = len([p for p in user_history["punishments"] if p["strefa"] == strefa])
        punishment_level = strefa_count

        # Znajdź odpowiednią rolę dla aktualnego poziomu kary
        strefa_roles = [p for p in PUNISHMENT_PROGRESSION if p['strefa_base'] == strefa]
        
        target_punishment = None
        if 0 < punishment_level <= len(strefa_roles):
            target_punishment = strefa_roles[punishment_level - 1]

        if not target_punishment:
            await ctx.send(f"Błąd: Nie znaleziono definicji kary dla strefy `{strefa}` na poziomie `{punishment_level}`. Sprawdź `PUNISHMENT_PROGRESSION` w kodzie.", delete_after=15)
            # Don't return, as we still want the escalation message to show up
        
        target_role_id = target_punishment.get("role_id") if target_punishment else None
        target_role = ctx.guild.get_role(target_role_id) if target_role_id else None

        # --- Usuń WSZYSTKIE inne role kar ---
        all_punishment_role_ids = {p.get("role_id") for p in PUNISHMENT_PROGRESSION if p.get("role_id")}
        roles_to_remove = []
        for role_id_to_check in all_punishment_role_ids:
            if role_id_to_check == target_role_id:
                continue
            role_to_check = ctx.guild.get_role(role_id_to_check)
            if role_to_check and role_to_check in member.roles:
                roles_to_remove.append(role_to_check)
        
        if roles_to_remove:
            try:
                await member.remove_roles(*roles_to_remove, reason="Progresywny system karania: aktualizacja roli.")
            except discord.Forbidden:
                await ctx.send(f"Nie mam uprawnień do usunięcia poprzednich ról kar. Upewnij się, że moja rola jest wyżej.", delete_after=10)
            except Exception as e:
                await ctx.send(f"Wystąpił błąd podczas usuwania poprzednich ról kar: {e}", delete_after=10)

        # --- Nadaj nową rolę kary ---
        if target_role:
            try:
                if target_role not in member.roles:
                    await member.add_roles(target_role, reason=f"Progresywny system karania: {target_punishment['name']}.")
                await ctx.send(f"Nadano rolę **{target_role.name}** dla {member.mention}.", delete_after=10)
                user_history["current_role_id"] = target_role_id
            except discord.Forbidden:
                await ctx.send(f"Nie mam uprawnień do nadania roli {target_role.name}. Upewnij się, że moja rola jest wyżej.", delete_after=10)
            except Exception as e:
                await ctx.send(f"Wystąpił błąd podczas nadawania roli {target_role.name}: {e}", delete_after=10)
        elif target_punishment: # Punishment exists but role not found
             await ctx.send(f"Nie znaleziono roli dla kary `{target_punishment['name']}` (ID: {target_role_id}). Sprawdź konfigurację.", delete_after=10)

        # --- Check for Escalation ---
        PUNISHMENT_THRESHOLDS = {
            "zielona": 3,
            "żółta": 3,
            "czerwona": 2
        }
        zielona_count = len([p for p in user_history["punishments"] if p["strefa"] == "zielona"])
        zolta_count = len([p for p in user_history["punishments"] if p["strefa"] == "żółta"])
        czerwona_count = len([p for p in user_history["punishments"] if p["strefa"] == "czerwona"])

        if strefa == "zielona" and zielona_count >= PUNISHMENT_THRESHOLDS["zielona"]:
            escalation_message = f"Użytkownik {member.mention} otrzymał już {zielona_count} ostrzeżeń w Strefie Zielonej. **Zasugeruj nadanie kary w Strefie Żółtej.**"
        elif strefa == "żółta" and zolta_count >= PUNISHMENT_THRESHOLDS["żółta"]:
            escalation_message = f"Użytkownik {member.mention} otrzymał już {zolta_count} ostrzeżeń w Strefie Żółtej. **Zasugeruj nadanie kary w Strefie Czerwonej.**"
        elif strefa == "czerwona" and czerwona_count >= PUNISHMENT_THRESHOLDS["czerwona"]:
            escalation_message = f"Użytkownik {member.mention} otrzymał już {czerwona_count} ostrzeżeń w Strefie Czerwonej. **Zasugeruj nadanie kary w Strefie Czarnej (ban).**"

    # --- Handle "Czarna" Zone (Ban) ---
    if strefa == "czarna":
        if not ctx.guild.me.guild_permissions.ban_members:
            return await ctx.send("Bot nie ma uprawnień do banowania użytkowników. Upewnij się, że ma uprawnienie 'Banuj członków'.")

        # Prepare and send DM embed before ban
        dm_embed_for_ban = discord.Embed(
            title="Zostałeś ZBANOWANY z serwera!",
            description=f"Zostałeś trwale zbanowany z serwera {ctx.guild.name} przez {ctx.author.mention}.\nPowód: {reason}",
            color=discord.Color.red(),
            timestamp=datetime.utcnow()
        )
        dm_embed_for_ban.set_thumbnail(url=member.display_avatar.url)
        dm_embed_for_ban.add_field(name="Administrator Karzący", value=ctx.author.mention, inline=False)
        dm_embed_for_ban.add_field(name="Powód", value=reason, inline=False)

        try:
            await member.send(embed=dm_embed_for_ban)
            await ctx.send(f"Wysłano DM do {member.mention} przed banem.")
        except discord.Forbidden:
            await ctx.send(f"Nie udało się wysłać DM do {member.mention} przed banem. Kontynuuję banowanie.")
        except Exception as e:
            await ctx.send(f"Wystąpił błąd podczas wysyłania DM przed banem: {e}. Kontynuuję banowanie.")

        try:
            await member.ban(reason=f"Strefa Czarna: {reason} (Administrator: {ctx.author.name})")
            await ctx.send(f"Użytkownik {member.mention} został **trwale zbanowany** ze względu na Strefę Czarną.")
            embed_title = "⛔ Użytkownik ZBANOWANY (Strefa Czarna)"
            embed_description = f"Użytkownik {member.mention} został trwale zbanowany z serwera przez {ctx.author.mention}."
            is_banned = True
            user_history = {"punishments": [], "current_role_id": None} # Reset history on ban
        except discord.Forbidden:
            return await ctx.send("Nie mam uprawnień do zbanowania tego użytkownika. Upewnij się, że moja rola jest wyżej niż rola banowanego użytkownika.")
        except Exception as e:
            return await ctx.send(f"Wystąpił błąd podczas banowania użytkownika: {e}")

    # --- Update Kara History ---
    kara_history[user_id_str] = user_history
    await _write_kara_history(kara_history)

    # --- Create Embed ---
    embed = discord.Embed(
        title=embed_title,
        description=embed_description,
        color=zone_info["color"],
        timestamp=datetime.utcnow()
    )
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Osoba karana", value=member.mention, inline=False)
    embed.add_field(name="Administrator Karzący", value=ctx.author.mention, inline=False)
    embed.add_field(name="Strefa kary", value=f"{zone_info['emoji']} {zone_info['name']}", inline=False)
    embed.add_field(name="Powód i Uzasadnienie", value=reason, inline=False)
    if mute_duration:
        embed.add_field(name="Czas wyciszenia", value=mute_duration, inline=False)
    if not is_banned: # Only show "Możliwość Odwołania" if not banned
        embed.add_field(name="Możliwość Odwołania", value=odwolanie, inline=False)
    if escalation_message:
        embed.add_field(name="Sugestia Eskalacji", value=escalation_message, inline=False)
    embed.set_footer(text=f"ID ukaranego: {member.id}")

    # --- Send to Log Channel ---
    if mod_log_channel_id:
        log_channel = bot.get_channel(mod_log_channel_id)
        if log_channel:
            await log_channel.send(embed=embed)
            if not is_banned: # Only send confirmation if not banned
                await ctx.send(f"Pomyślnie utworzono ostrzeżenie dla {member.mention} na kanale {log_channel.mention}.", delete_after=5)
        else:
            await ctx.send("Nie znaleziono kanału z logami. Karta nie została wysłana.")

    # --- Send DM to User ---
    if not is_banned: # Only send DM if not already banned and DM'd
        try:
            dm_embed = embed.copy()
            dm_embed.title = "Otrzymałeś nowe ostrzeżenie"
            await member.send(embed=dm_embed)
        except discord.Forbidden:
            await ctx.send("(Nie udało się wysłać DM do użytkownika.)", delete_after=10)

@kara.error
async def kara_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("Brak argumentów. Użycie: `!kara <strefa> <użytkownik> <TAK/NIE> [czas wyciszenia] <powód>`")
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send("Nie znaleziono takiego użytkownika.")
    elif isinstance(error, commands.MissingPermissions):
        await ctx.send("Nie masz uprawnień do zarządzania wiadomościami.")
    else:
        print(f"Wystąpił błąd w komendzie kara: {error}")
        await ctx.send("Wystąpił nieoczekiwany błąd.")

@bot.command(name="historia")
@commands.has_permissions(manage_messages=True)
async def historia(ctx, member: discord.Member):
    """Wyświetla historię moderacyjną użytkownika."""
    kara_history = await _read_kara_history()
    user_id_str = str(member.id)
    user_history = kara_history.get(user_id_str)

    if not user_history or not user_history.get("punishments"):
        embed = discord.Embed(
            title=f"Historia moderacyjna dla {member.display_name}",
            description="Ten użytkownik ma czystą historię, brak nałożonych kar.",
            color=discord.Color.green()
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"ID Użytkownika: {member.id}")
        await ctx.send(embed=embed)
        return

    punishments = user_history["punishments"]
    punishments.reverse() # Show newest first

    # Pagination
    pages = []
    items_per_page = 5
    for i in range(0, len(punishments), items_per_page):
        page_punishments = punishments[i:i + items_per_page]
        embed = discord.Embed(
            title=f"Historia moderacyjna dla {member.display_name}",
            color=discord.Color.orange()
        )
        embed.set_thumbnail(url=member.display_avatar.url)

        for p in page_punishments:
            date = datetime.fromisoformat(p['date']).strftime('%Y-%m-%d %H:%M:%S')
            mute_info = f"\n**Wyciszenie:** {p['mute_duration']}" if p.get('mute_duration') else ""
            embed.add_field(
                name=f"**{p['strefa'].capitalize()}** - {date}",
                value=f"**Powód:** {p['reason']}\n**Moderator:** {p['moderator']}{mute_info}",
                inline=False
            )
        
        pages.append(embed)

    current_page = 0
    pages[current_page].set_footer(text=f"Strona {current_page + 1}/{len(pages)} | ID Użytkownika: {member.id}")
    message = await ctx.send(embed=pages[current_page])

    if len(pages) > 1:
        await message.add_reaction("◀️")
        await message.add_reaction("▶️")

        def check(reaction, user):
            return user == ctx.author and str(reaction.emoji) in ["◀️", "▶️"] and reaction.message.id == message.id

        while True:
            try:
                reaction, user = await bot.wait_for("reaction_add", timeout=60.0, check=check)

                if str(reaction.emoji) == "▶️" and current_page < len(pages) - 1:
                    current_page += 1
                elif str(reaction.emoji) == "◀️" and current_page > 0:
                    current_page -= 1
                
                pages[current_page].set_footer(text=f"Strona {current_page + 1}/{len(pages)} | ID Użytkownika: {member.id}")
                await message.edit(embed=pages[current_page])
                await message.remove_reaction(reaction, user)

            except asyncio.TimeoutError:
                await message.clear_reactions()
                break

@historia.error
async def historia_error(ctx, error):
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send("Musisz podać użytkownika. Użycie: `!historia <użytkownik>`")
    elif isinstance(error, commands.MemberNotFound):
        await ctx.send("Nie znaleziono takiego użytkownika na tym serwerze.")
    elif isinstance(error, commands.MissingPermissions):
        await ctx.send("Nie masz uprawnień do używania tej komendy.")
    else:
        print(f"Wystąpił błąd w komendzie historia: {error}")
        await ctx.send("Wystąpił nieoczekiwany błąd.")

# --- Logika Gry "Jaka to melodia?" ---
SONG_LIST = [
    "Sanah - Szampan", "Dawid Podsiadło - Małomiasteczkowy", "Taco Hemingway - Polskie Tango",
    "Daria Zawiałow - Kaonashi", "Quebonafide - Bubbletea", "The Weeknd - Blinding Lights",
    "Dua Lipa - Don't Start Now", "Harry Styles - As It Was", "Glass Animals - Heat Waves",
    "Imagine Dragons - Believer"
]

class GuessTheSongGame:
    def __init__(self, ctx, voice_client):
        self.ctx = ctx
        self.voice_client = voice_client
        self.song_info = random.choice(SONG_LIST)
        self.answer = self.song_info.lower()
        self.winner = None
        self.bot = ctx.bot

    async def start_game(self):
        global game_instance
        await self.ctx.send("**Jaka to melodia?** Za chwilę usłyszycie fragment piosenki. Kto pierwszy odgadnie tytuł i wykonawcę, wygrywa!")
        await asyncio.sleep(2)
        try:
            await self._play_song_fragment()
        except Exception as e:
            await self.ctx.send(f"Nie udało się odtworzyć piosenki. Spróbuj ponownie. Błąd: {e}")
            game_instance = None
            return
        await asyncio.sleep(15)
        if not self.winner:
            await self.ctx.send(f"Nikt nie odgadł! Prawidłowa odpowiedź to: **{game_instance.song_info}**")
        if self.voice_client.is_playing():
            self.voice_client.stop()
        game_instance = None

    def _download_song(self):
        ydl_opts = {
            'format': 'bestaudio/best',
            'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '192'}],
            'outtmpl': './%(id)s.%(ext)s', 'default_search': 'ytsearch', 'noplaylist': True,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(self.song_info, download=False)
            if 'entries' in info:
                info = info['entries'][0]
            return info['url']

    async def _play_song_fragment(self):
        url = await self.bot.loop.run_in_executor(None, self._download_song)
        ffmpeg_options = {
            'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
            'options': '-vn -ss 30 -t 15 -loglevel panic'
        }
        self.voice_client.play(discord.FFmpegPCMAudio(url, **ffmpeg_options))

    def check_answer(self, message):
        similarity = fuzz.ratio(message.content.lower(), self.answer)
        if similarity > 80:
            self.winner = message.author
            return True
        return False

@bot.command()
async def zgaduj(ctx):
    global game_instance
    if game_instance:
        await ctx.send("Gra już trwa! Poczekaj na jej zakończenie.")
        return
    vc = await get_voice_client(ctx)
    if not vc:
        return
    game_instance = GuessTheSongGame(ctx, vc)
    await game_instance.start_game()

@bot.command()
async def ranking(ctx):
    if not scores:
        await ctx.send("Nikt jeszcze nie zdobył punktów. Zagraj w `!zgaduj` lub `!trivia`!")
        return
    sorted_scores = sorted(scores.items(), key=lambda item: item[1], reverse=True)
    embed = discord.Embed(title="🏆 Ranking Gier", color=discord.Color.gold())
    for i, (user_id, score) in enumerate(sorted_scores, 1):
        try:
            user = await bot.fetch_user(user_id)
            embed.add_field(name=f"#{i} {user.name}", value=f"**{score}** punktów", inline=False)
        except discord.NotFound:
            embed.add_field(name=f"#{i} Użytkownik (ID: {user_id})", value=f"**{score}** punktów", inline=False)
    await ctx.send(embed=embed)

# --- Logika Gry "Trivia" ---
trivia_questions = []
trivia_game_instance = None

def load_trivia_questions():
    global trivia_questions
    try:
        with open(os.path.join(DATA_DIR, 'trivia_questions.json'), 'r', encoding='utf-8') as f:
            trivia_questions = json.load(f)
        print(f"Załadowano {len(trivia_questions)} pytań do trivii.")
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"Błąd podczas ładowania pytań do trivii: {e}")

class TriviaView(discord.ui.View):
    def __init__(self, game):
        super().__init__(timeout=30.0) # 30 sekund na odpowiedź
        self.game = game
        self.buttons = []
        self.create_buttons()

    def create_buttons(self):
        options = self.game.options
        for i, option in enumerate(options):
            button = discord.ui.Button(label=option, style=discord.ButtonStyle.primary, custom_id=f"trivia_{i}")
            button.callback = self.button_callback
            self.add_item(button)
            self.buttons.append(button)

    async def button_callback(self, interaction: discord.Interaction):
        if self.game.winner:
            await interaction.response.send_message("Ktoś już odpowiedział na to pytanie!", ephemeral=True)
            return

        chosen_option = interaction.data['label']
        self.game.winner = interaction.user

        # Wyłącz wszystkie przyciski po odpowiedzi
        for button in self.buttons:
            button.disabled = True

        if chosen_option == self.game.correct_answer:
            scores[self.game.winner.id] = scores.get(self.game.winner.id, 0) + 1
            await interaction.message.edit(content=f"🎉 Poprawna odpowiedź! **{self.game.winner.mention}** zdobywa punkt! 🎉\nPrawidłowa odpowiedź to: **{self.game.correct_answer}**", view=self)
        else:
            await interaction.message.edit(content=f"❌ Zła odpowiedź! Prawidłowa odpowiedź to: **{self.game.correct_answer}**", view=self)
        
        self.stop()

    async def on_timeout(self):
        if not self.game.winner:
            for button in self.buttons:
                button.disabled = True
            await self.message.edit(content=f"⏰ Czas minął! Prawidłowa odpowiedź to: **{self.game.correct_answer}**", view=self)

class TriviaGame:
    def __init__(self, ctx):
        self.ctx = ctx
        self.question_data = random.choice(trivia_questions)
        self.question = self.question_data['question']
        self.correct_answer = self.question_data['correct_answer']
        self.options = self.question_data['incorrect_answers'] + [self.correct_answer]
        random.shuffle(self.options)
        self.winner = None

    async def start_game(self):
        global trivia_game_instance
        embed = discord.Embed(title="🧠 Trivia Time!", description=self.question, color=discord.Color.purple())
        view = TriviaView(self)
        message = await self.ctx.send(embed=embed, view=view)
        view.message = message
        await view.wait()
        trivia_game_instance = None # Zresetuj grę po zakończeniu

@bot.command()
async def trivia(ctx):
    global trivia_game_instance
    if trivia_game_instance:
        await ctx.send("Gra w trivię już trwa! Poczekaj na jej zakończenie.")
        return
    if not trivia_questions:
        await ctx.send("Brak pytań do trivii. Poproś administratora o dodanie pytań.")
        return
    
    trivia_game_instance = TriviaGame(ctx)
    await trivia_game_instance.start_game()

@bot.command(name='poll', aliases=['ankieta'])
async def poll(ctx, question: str, *options: str):
    """Creates a poll with a question and multiple options."""
    if len(options) < 2:
        await ctx.send("Musisz podać co najmniej 2 opcje dla ankiety.")
        return
    if len(options) > 10:
        await ctx.send("Możesz podać maksymalnie 10 opcji.")
        return

    embed = discord.Embed(
        title="📊 Ankieta",
        description=f"**{question}**",
        color=discord.Color.dark_purple()
    )

    for i, option in enumerate(options):
        embed.add_field(name=f"{i+1}\ufe0f\u20e3 {option}", value="\u200b", inline=False)

    embed.set_footer(text=f"Ankieta stworzona przez {ctx.author.display_name}")
    
    try:
        poll_message = await ctx.send(embed=embed)
        for i in range(len(options)):
            await poll_message.add_reaction(f"{i+1}\ufe0f\u20e3")
    except discord.Forbidden:
        await ctx.send("Nie mam uprawnień do dodawania reakcji.")
    except Exception as e:
        await ctx.send(f"Wystąpił błąd podczas tworzenia ankiety: {e}")

# --- Konfiguracja Radia ---




@bot.command(aliases=['np', 'teraz'])
async def nowplaying(ctx):
    if music_player and music_player.current_track:
        await ctx.send(f"▶️ Odtwarzam: **{music_player.current_track}**")
    else:
        await ctx.send("Obecnie nic nie gram. Użyj komendy `!play`, aby coś włączyć.")

# --- Soundboard ---
SOUNDS = {}

async def load_sounds():
    global SOUNDS
    
    def _load_sounds_sync():
        sounds_dir = os.path.join(DATA_DIR, "sounds")
        if not os.path.exists(sounds_dir):
            os.makedirs(sounds_dir)
        
        loaded_sounds = {}
        for filename in os.listdir(sounds_dir):
            if filename.endswith((".mp3", ".wav", ".ogg")):
                sound_name = os.path.splitext(filename)[0].lower()
                loaded_sounds[sound_name] = os.path.join(sounds_dir, filename)
        return loaded_sounds

    SOUNDS = await bot.loop.run_in_executor(None, _load_sounds_sync)
    print(f"Załadowano {len(SOUNDS)} dźwięków z folderu 'sounds'.")

@bot.command()
async def sound(ctx, sound_name: str, voice_client: discord.VoiceClient = None):
    sound_name = sound_name.lower()
    if sound_name not in SOUNDS:
        await ctx.send(f"Nie znam dźwięku '{sound_name}'. Dostępne: {', '.join(SOUNDS.keys())}")
        return
    
    vc = voice_client
    if not vc:
        vc = await get_voice_client(ctx)
        if not vc:
            return

    if vc.is_playing():
        vc.stop()
    try:
        source = discord.FFmpegPCMAudio(SOUNDS[sound_name])
        vc.play(source, after=lambda e: print(f"Błąd odtwarzania: {e}" if e else None))
        await ctx.send(f"Odtwarzam: **{sound_name}**!")
    except Exception as e:
        await ctx.send(f"Wystąpił błąd podczas odtwarzania: {e}")

@bot.command(aliases=['sounds'])
async def listsounds(ctx, page: int = 1):
    if not SOUNDS:
        await ctx.send("Soundboard jest pusty.")
        return
    
    items_per_page = 15
    sorted_sounds = sorted(SOUNDS.keys())
    pages = [sorted_sounds[i:i + items_per_page] for i in range(0, len(sorted_sounds), items_per_page)]
    
    if page < 1 or page > len(pages):
        await ctx.send(f"Nieprawidłowy numer strony. Dostępne strony: 1-{len(pages)}")
        return

    embed = discord.Embed(
        title="Dostępne dźwięki (Soundboard)",
        description=", ".join(f"`{name}`" for name in pages[page-1]),
        color=discord.Color.orange()
    )
    embed.set_footer(text=f"Strona {page}/{len(pages)} | Użyj !sound <nazwa>, aby odtworzyć.")
    await ctx.send(embed=embed)

@bot.command(name='user_info', aliases=['user-info', 'ui'])
async def user_info(ctx, member: discord.Member = None):
    """Displays information about a user."""
    if member is None:
        member = ctx.author

    embed = discord.Embed(title=f"Informacje o {member.name}", color=member.color)
    embed.set_thumbnail(url=member.display_avatar.url)
    embed.add_field(name="Nazwa", value=f"{member.name}#{member.discriminator}", inline=True)
    if member.nick:
        embed.add_field(name="Nick", value=member.nick, inline=True)
    embed.add_field(name="ID", value=member.id, inline=False)
    embed.add_field(name="Utworzono konto", value=member.created_at.strftime("%d.%m.%Y %H:%M"), inline=True)
    embed.add_field(name="Dołączono do serwera", value=member.joined_at.strftime("%d.%m.%Y %H:%M"), inline=True)
    
    roles = [role.mention for role in member.roles[1:]]
    if roles:
        roles_str = ", ".join(reversed(roles))
        if len(roles_str) > 1024:
            roles_str = roles_str[:1020] + "... "
        embed.add_field(name=f"Role [{len(roles)}]", value=roles_str, inline=False)

    status_emoji = {
        discord.Status.online: "🟢 Online",
        discord.Status.idle: "🌙 Zaraz wracam",
        discord.Status.dnd: "⛔ Nie przeszkadzać",
        discord.Status.offline: "⚫ Offline",
        discord.Status.invisible: "⚫ Offline"
    }
    embed.add_field(name="Status", value=status_emoji.get(member.status, "❔ Nieznany"), inline=True)

    if member.activity:
        if member.activity.type == discord.ActivityType.playing:
            activity_name = "Gra w"
        elif member.activity.type == discord.ActivityType.listening:
            activity_name = "Słucha"
        elif member.activity.type == discord.ActivityType.watching:
            activity_name = "Ogląda"
        elif member.activity.type == discord.ActivityType.streaming:
            activity_name = "Streamuje"
        else:
            activity_name = "Aktywność"
        embed.add_field(name=activity_name, value=member.activity.name, inline=True)

    embed.set_footer(text=f"Zapytano przez {ctx.author.name}", icon_url=ctx.author.display_avatar.url)
    await ctx.send(embed=embed)


#@bot.command(name='setup_server')
@commands.has_permissions(administrator=True)
async def setup_server(ctx):
    """Tworzy lub odświeża pełną strukturę serwera dla sklepu z botami, usuwając poprzednią konfigurację i dodając treść."""
    guild = ctx.guild
    await ctx.send("Rozpoczynam pełną rekonfigurację serwera...")

    CATEGORIES_TO_DELETE = [
        "INFO", "🛒 SKLEP Z BOTAMI", "🤖 SPOŁECZNOŚĆ",
        "🎓 STREFA DEWELOPERA", "🎤 KANAŁY GŁOSOWE"
    ]

    SERVER_STRUCTURE = {
        "🛒 SKLEP Z BOTAMI": {
            "type": "text",
            "channels": [
                {
                    "name": "👋-powitalnia",
                    "content": {
                        "title": "O Nas",
                        "description": "Jesteśmy zespołem pasjonatów programowania, specjalizującym się w tworzeniu niestandardowych botów Discord. Naszym celem jest dostarczenie rozwiązań, które idealnie odpowiadają na potrzeby Twojej społeczności lub biznesu, od prostych narzędzi po zaawansowane systemy.\n\nStawiamy na jakość, przejrzystość i indywidualne podejście do każdego projektu. Skontaktuj się z nami i opowiedz o swoim pomyśle!"
                    }
                },
                { "name": "📢-ogłoszenia", "content": None },
                {
                    "name": "📜-regulamin-sklepu",
                    "content": {
                        "title": "Regulamin Sklepu",
                        "description": "Tutaj powinien znaleźć się regulamin Twojego sklepu. Skontaktuj się z administratorem, aby go uzupełnić."
                    }
                },
                {
                    "name": "💰-cennik",
                    "content": {
                        "title": "Cennik i Hosting",
                        "description": """*Ceny są orientacyjne i zależą od złożoności funkcji oraz czasu realizacji.*

**⭐ Prosty bot (Od 20 zł do 80 zł)**
Idealny do podstawowych zadań.
> ✓ Wyświetlanie zasad
> ✓ Proste powiadomienia
> ✓ Automatyczne role
> ✓ Podstawowe odpowiedzi na pytania

**📊 Średnio zaawansowany bot (Od 100 zł do 150 zł)**
Większa interaktywność i integracje.
> ✓ System ticketów
> ✓ Prosta ekonomia serwera
> ✓ Integracja z zewnętrznymi API (pogoda, statystyki gier)
> ✓ Zarządzanie prostymi danymi

**🚀 Zaawansowany bot (Od 150 zł wzwyż)**
Złożone systemy i rozbudowane funkcje.
> ✓ Rozbudowane gry ekonomiczne
> ✓ Integracje z wieloma platformami
> ✓ Zaawansowane systemy moderacji z AI
> ✓ Złożone systemy analityczne

*Ostateczny koszt zależy od szczegółów projektu, doświadczenia twórcy i jego stawek. Do tego dochodzi koszt hostingu!*

---

### Hosting dla Twojego Bota
*Aby Twój bot działał 24/7, musi być hostowany na serwerze.*

**🖥️ VPS (Virtual Private Server) - Zalecane! (Od 20 zł do 100+ zł miesięcznie)**
Jeśli nie czujesz się na siłach, skonfigurujemy hosting za Ciebie! Oferuje stabilność, kontrolę i wydajność.
> ✓ będziesz posiadał dostęp do bota ale my go hostujemy (max ping u nas to 114ms)
> ✓ Idealny dla botów średnich i dużych
> ✓ Dostawcy: OVHcloud, DigitalOcean, Hetzner, Contabo

**💻 Własny komputer/Serwer domowy (Koszt prądu, sprzętu)**
Opcja do testów i nauki. Bot działa tylko, gdy komputer jest włączony i ma dostęp do internetu.
> ✓ Nie zalecane dla botów działających 24/7 bez przerw
> ✓ Pełna kontrola nad środowiskiem

*Ceny zależą od zasobów (RAM, procesor, dysk), transferu danych i konkretnego dostawcy. Zawsze sprawdzaj aktualne oferty!*
"""
                    }
                },
                { "name": "🤖-portfolio", "content": None },
                { "name": "✅-opinie-klientów", "content": None },
                {
                    "name": "✉️-złóż-zamówienie",
                    "content": {
                        "title": "Skontaktuj się z nami!",
                        "description": """Chętnie odpowiemy na Twoje pytania i omówimy szczegóły projektu.

**Discord:** Zapraszamy na naszego discorda gdzie złożysz zamówienie! [Kliknij aby dołączyć](https://discord.gg/DfMsrdh77v)
**E-mail:** botydiscord244@gmail.com
**Strona WWW / Portfolio:** [Odwiedź portfolio](https://botydiscord.unaux.com/)

*Jesteśmy dostępni, aby stworzyć bota Twoich marzeń!*
"""
                    }
                }
            ]
        },
        "🤖 SPOŁECZNOŚĆ": {
            "type": "text",
            "channels": [
                {"name": "💬-pogaduszki", "content": None},
                {"name": "💡-propozycje-i-pomysły", "content": None},
                {"name": "🎉-konkursy-i-eventy", "content": None}
            ]
        },
        "🎓 STREFA DEWELOPERA": {
            "type": "text",
            "channels": [
                {"name": "💻-pomoc-w-kodowaniu", "content": None},
                {"name": "🔗-przydatne-linki", "content": None},
                {
                    "name": "❓-faq",
                    "content": {
                        "title": "Najczęściej Zadawane Pytania (FAQ)",
                        "description": """**1. Ile trwa stworzenie prostego bota?**
> Czas realizacji prostego bota to zazwyczaj od 3 do 7 dni roboczych, w zależności od złożoności funkcji.

**2. Czy boty wymagają stałej opieki lub aktualizacji?**
> Tak, boty wymagają regularnych aktualizacji bibliotek i czasami dostosowania do zmian w API Discorda. Oferujemy pakiety wsparcia.

**3. Czy mogę zamówić funkcję niestandardową?**
> Absolutnie! Specjalizujemy się w niestandardowych rozwiązaniach. Opisz nam swój pomysł, a my przygotujemy wycenę.

**4. Jakie są wymagania techniczne dla mojego bota?**
> Większość botów Pythona działa dobrze na standardowych VPSach. Kluczowe są odpowiednia ilość RAM i stabilne połączenie internetowe. Nasz bot hostingowy (!hostingbota) podaje więcej szczegółów.
"""
                    }
                }
            ]
        },
        "🎤 KANAŁY GŁOSOWE": {
            "type": "voice",
            "channels": [
                {"name": "🔊-Rozmowy", "content": None},
                {"name": "🔊-Wspólne kodowanie", "content": None},
                {"name": "🔊-Poczekalnia", "content": None}
            ]
        }
    }


    # --- ETAP 1: Usuwanie istniejącej struktury ---
    await ctx.send("Etap 1/3: Usuwanie poprzedniej konfiguracji...")
    for category_name in reversed(CATEGORIES_TO_DELETE):
        category = discord.utils.get(guild.categories, name=category_name)
        if category:
            await ctx.send(f"  - Znaleziono kategorię: `{category_name}`. Usuwam kanały...")
            for channel in list(category.channels):
                try:
                    await channel.delete(reason="Rekonfiguracja serwera")
                except Exception:
                    pass # Ignore errors, we are cleaning up
            try:
                await category.delete(reason="Rekonfiguracja serwera")
                await ctx.send(f"  - Usunięto kategorię: `{category_name}`")
            except Exception as e:
                await ctx.send(f"  - Błąd przy usuwaniu kategorii `{category_name}`: {e}")

    # --- ETAP 2: Tworzenie nowej struktury ---
    await ctx.send("Etap 2/3: Tworzenie nowej struktury kanałów...")
    for category_name, category_data in SERVER_STRUCTURE.items():
        try:
            category = await guild.create_category(category_name)
            await ctx.send(f"  - Stworzono kategorię: `{category_name}`")

            channel_type = category_data["type"]
            for channel_info in category_data["channels"]:
                channel_name = channel_info["name"]
                try:
                    if channel_type == "text":
                        new_channel = await guild.create_text_channel(channel_name, category=category)
                    else: # voice
                        new_channel = await guild.create_voice_channel(channel_name, category=category)
                    await ctx.send(f"    - Stworzono kanał: `{channel_name}`")
                    channel_info['object'] = new_channel
                except Exception as e:
                    await ctx.send(f"    - Błąd przy tworzeniu kanału `{channel_name}`: {e}")
        except Exception as e:
            await ctx.send(f"  - Błąd przy tworzeniu kategorii `{category_name}`: {e}")
    
    await asyncio.sleep(2)

    # --- ETAP 3: Wypełnianie kanałów treścią ---
    await ctx.send("Etap 3/3: Wypełnianie kanałów treścią...")
    for category_name, category_data in SERVER_STRUCTURE.items():
        if category_data["type"] == "text":
            for channel_info in category_data["channels"]:
                if channel_info.get("content") and channel_info.get("object"):
                    channel = channel_info["object"]
                    channel_content = channel_info["content"]
                    try:
                        embed = discord.Embed(
                            title=channel_content["title"],
                            description=channel_content["description"],
                            color=discord.Color.blue()
                        )
                        await channel.send(embed=embed)
                        await ctx.send(f"  - Wypełniono kanał: `{channel.name}`")
                    except Exception as e:
                        await ctx.send(f"  - Błąd przy wysyłaniu wiadomości do `{channel.name}`: {e}")

    await ctx.send("✅ Rekonfiguracja serwera została zakończona!")

#@setup_server.error
async def setup_server_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("Nie masz uprawnień do użycia tej komendy.")
    else:
        print(f"Błąd w setup_server: {error}") # Log the full error
        await ctx.send(f"Wystąpił nieoczekiwany błąd. Sprawdź logi bota.")



@bot.command()
@commands.has_permissions(manage_messages=True)
async def addsound(ctx, name: str):
    if len(ctx.message.attachments) == 0:
        await ctx.send("Musisz załączyć plik audio (mp3, wav, ogg).")
        return

    attachment = ctx.message.attachments[0]
    if not attachment.filename.lower().endswith((".mp3", ".wav", ".ogg")):
        await ctx.send("Nieprawidłowy format pliku. Dozwolone formaty: mp3, wav, ogg.")
        return

    sound_name = name.lower()
    if sound_name in SOUNDS:
        await ctx.send(f"Dźwięk o nazwie '{sound_name}' już istnieje. Użyj innej nazwy.")
        return

    sounds_dir = os.path.join(DATA_DIR, "sounds")
    if not os.path.exists(sounds_dir):
        os.makedirs(sounds_dir)

    file_path = os.path.join(sounds_dir, f"{sound_name}{os.path.splitext(attachment.filename)[1]}")
    
    try:
        await attachment.save(file_path)
        await load_sounds()
        await ctx.send(f"Dodano nowy dźwięk: `{sound_name}`")
    except Exception as e:
        await ctx.send(f"Wystąpił błąd podczas zapisywania pliku: {e}")

@bot.command()
@commands.has_permissions(manage_messages=True)
async def removesound(ctx, sound_name: str):
    sound_name = sound_name.lower()
    if sound_name not in SOUNDS:
        await ctx.send(f"Nie znaleziono dźwięku o nazwie '{sound_name}'.")
        return

    file_path = SOUNDS[sound_name]
    try:
        os.remove(file_path)
        await load_sounds()
        await ctx.send(f"Usunięto dźwięk: `{sound_name}`")
    except Exception as e:
        await ctx.send(f"Wystąpił błąd podczas usuwania pliku: {e}")

# --- Eventy Bota ---
from discord.ext import tasks

# --- Lista statusów do rotacji ---
STATUS_LIST = [
    discord.Activity(type=discord.ActivityType.listening, name="!help"),
    discord.Activity(type=discord.ActivityType.watching, name="debaty na YouTube"),
    discord.Game(name="w Zgadywankę (!zgaduj)"),
]

@tasks.loop(minutes=15)
async def rotate_status():
    """Automatycznie zmienia status bota co 15 minut."""
    new_status = random.choice(STATUS_LIST)
    # Unikaj zmiany statusu, jeśli bot aktywnie coś odtwarza (muzykę, radio, soundboard)
    voice_client_active = any(vc.is_playing() for vc in bot.voice_clients)
    if not voice_client_active:
        await bot.change_presence(activity=new_status)
        print(f"Zmieniono status na: {new_status.type.name} {new_status.name}")

@rotate_status.before_loop
async def before_rotate_status():
    """Poczekaj, aż bot będzie gotowy."""
    await bot.wait_until_ready()


@tasks.loop(seconds=15)
async def update_status_file():
    """Co 15 sekund zapisuje plik statusu, aby zasygnalizować, że bot działa."""
    print("--- Heartbeat: Próba zapisu statusu ---")
    # Workaround for clock drift: add 2 hours to the timestamp
    now_plus_2h = datetime.utcnow() + timedelta(hours=2)
    status_data = {
        "pid": os.getpid(),
        "timestamp": now_plus_2h.timestamp(),
        "datetime_utc": now_plus_2h.isoformat()
    }
    try:
        def _write_bot_status(data):
            filepath = os.path.join(DATA_DIR, "bot_status.json")
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f)
        
        await bot.loop.run_in_executor(None, functools.partial(_write_bot_status, status_data))
        print(f"--- Heartbeat: Status pomyślnie zapisany o {status_data['datetime_utc']} ---")
    except Exception as e:
        print(f"--- Heartbeat ERROR: Błąd podczas zapisu pliku statusu: {e} ---")

@update_status_file.before_loop
async def before_update_status_file():
    """Poczekaj, aż bot będzie gotowy."""
    await bot.wait_until_ready()


@tasks.loop(seconds=5)
async def update_dashboard_data():
    """Co 5 sekund zapisuje dane dla panelu (głos, muzyka)."""
    await bot.wait_until_ready()
    
    # --- Status Kanałów Głosowych ---
    voice_data = {}
    for guild in bot.guilds:
        guild_channels = {}
        for channel in guild.voice_channels:
            if channel.members:
                member_names = [member.display_name for member in channel.members]
                guild_channels[channel.name] = member_names
        if guild_channels:
            voice_data[guild.name] = guild_channels
    
    try:
        with open(os.path.join(DATA_DIR, "voice_status.json"), "w", encoding="utf-8") as f:
            json.dump(voice_data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"Błąd podczas zapisu pliku voice_status.json: {e}")

    # --- Status Muzyki ---
    music_data = {
        "is_playing": False,
        "current_track": "Brak",
        "queue": []
    }
    global music_player
    if music_player and music_player.voice_client:
        music_data["is_playing"] = music_player.voice_client.is_playing()
        if music_player.current_track:
            music_data["current_track"] = music_player.current_track
        if not music_player.queue.empty():
            music_data["queue"] = list(music_player.queue._queue)
            
    try:
        with open(os.path.join(DATA_DIR, "music_status.json"), "w", encoding="utf-8") as f:
            json.dump(music_data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"Błąd podczas zapisu pliku music_status.json: {e}")

    # --- Status Soundboardu ---
    soundboard_data = {
        "sounds": list(SOUNDS.keys())
    }
    try:
        with open(os.path.join(DATA_DIR, "soundboard_status.json"), "w", encoding="utf-8") as f:
            json.dump(soundboard_data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"Błąd podczas zapisu pliku soundboard_status.json: {e}")

    # --- Lista Serwerów ---
    servers_data = []
    for guild in bot.guilds:
        servers_data.append({
            "id": str(guild.id),
            "name": guild.name
        })
    try:
        with open(os.path.join(DATA_DIR, "servers.json"), "w", encoding="utf-8") as f:
            json.dump(servers_data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"Błąd podczas zapisu pliku servers.json: {e}")

    # --- Status Użytkowników ---
    users_data = []
    for guild in bot.guilds:
        for member in guild.members:
            users_data.append({
                "id": member.id,
                "name": member.name,
                "discriminator": member.discriminator,
                "nick": member.nick,
                "roles": [role.name for role in member.roles]
            })
    try:
        with open(os.path.join(DATA_DIR, "users.json"), "w", encoding="utf-8") as f:
            json.dump(users_data, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"Błąd podczas zapisu pliku users.json: {e}")

@tasks.loop(seconds=5)
async def check_panel_commands():
    """Sprawdza, czy panel webowy wysłał jakąś komendę."""
    await bot.wait_until_ready()

    try:
        with open(os.path.join(DATA_DIR, 'servers.json'), 'r', encoding='utf-8') as f:
            server_configs.update(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        pass # Keep the existing (potentially empty) config

    web_panel_url = config.get("WEB_PANEL_URL")
    if not web_panel_url:
        return

    for guild in bot.guilds:
        guild_id = str(guild.id)
        if guild_id not in server_configs:
            continue

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"{web_panel_url}/api/commands/{guild_id}") as response:
                    if response.status == 200:
                        commands = await response.json()
                        for command_data in commands:
                            command = command_data.get("command")
                            print(f"Otrzymano komendę z panelu dla serwera {guild.name}: {command}")

                            # --- Logika wykonywania komend ---
                            if command == "skip":
                                if music_player and music_player.voice_client and music_player.voice_client.is_playing():
                                    music_player.voice_client.stop()
                                    print(f"Wykonano 'skip' z panelu na serwerze {guild.name}.")
                            elif command == "clear_queue":
                                if music_player:
                                    music_player.queue = asyncio.Queue()
                                    print(f"Wyczyszczono kolejkę na serwerze {guild.name}.")

                    elif response.status != 404: # Ignore 404 Not Found, as it's normal if there are no commands
                        print(f"Błąd podczas odpytywania panelu dla serwera {guild.name}: {response.status}")
        except Exception as e:
            print(f"Błąd w pętli sprawdzania komend panelu dla serwera {guild.name}: {e}")

@update_dashboard_data.before_loop
async def before_update_dashboard_data():
    """Poczekaj, aż bot będzie gotowy."""
    await bot.wait_until_ready()

dashboard_message = None

@tasks.loop(seconds=20)
async def update_discord_dashboard():
    await bot.wait_until_ready()
    global dashboard_message

    # --- Wczytanie konfiguracji ---
    try:
        with open(os.path.join(DATA_DIR, 'config.json'), 'r', encoding='utf-8') as f:
            config = json.load(f)
        dashboard_channel_id = config.get("DASHBOARD_CHANNEL_ID")
        if not dashboard_channel_id:
            return # Zakończ, jeśli kanał nie jest skonfigurowany
    except (FileNotFoundError, json.JSONDecodeError):
        return

    channel = bot.get_channel(dashboard_channel_id)
    if not channel:
        print(f"Błąd Dashboardu: Nie znaleziono kanału o ID {dashboard_channel_id}")
        return

    # --- Zbieranie danych ---
    guild = channel.guild
    online_members = sum(1 for m in guild.members if m.status != discord.Status.offline)
    
    # Dane o muzyce
    np_track = "Cisza"
    queue_list = []
    if music_player and music_player.voice_client and music_player.voice_client.is_playing():
        if music_player.current_track:
            np_track = music_player.current_track
        if not music_player.queue.empty():
            queue_list = list(music_player.queue._queue)[:5] # Pokaż do 5 następnych utworów

    # Dane o kanałach głosowych
    voice_activity_lines = []
    for vc in guild.voice_channels:
        if vc.members:
            member_names = [m.display_name for m in vc.members]
            voice_activity_lines.append(f"**{vc.name}** ({len(member_names)}): {', '.join(member_names)}")

    # --- Tworzenie Embeda ---
    embed = discord.Embed(title=f"Status Serwera {guild.name}", color=discord.Color.blue(), timestamp=datetime.now(ZoneInfo("Europe/Warsaw")))
    embed.set_thumbnail(url=guild.icon.url if guild.icon else None)
    embed.add_field(name="Użytkownicy Online", value=f"🟢 {online_members} / {guild.member_count}", inline=True)
    embed.add_field(name="Status Bota", value="✅ Aktywny", inline=True)
    
    embed.add_field(name="🎧 Teraz Grane", value=np_track, inline=False)
    if queue_list:
        queue_str = "\n".join(f"{i+1}. {song}" for i, song in enumerate(queue_list))
        embed.add_field(name="Kolejka", value=queue_str, inline=False)

    if voice_activity_lines:
        embed.add_field(name="👥 Aktywność Głosowa", value="\n".join(voice_activity_lines), inline=False)
    

    embed.set_footer(text="Automatyczna aktualizacja co 20 sekund")

    # --- Aktualizacja wiadomości ---
    try:
        if dashboard_message:
            await dashboard_message.edit(embed=embed)
        else:
            # Spróbuj znaleźć starą wiadomość
            async for msg in channel.history(limit=10):
                if msg.author == bot.user:
                    dashboard_message = msg
                    await dashboard_message.edit(embed=embed)
                    return
            # Jeśli nie ma, stwórz nową
            dashboard_message = await channel.send(embed=embed)
    except discord.NotFound:
        # Wiadomość została usunięta, stwórz nową
        dashboard_message = await channel.send(embed=embed)
    except Exception as e:
        print(f"Błąd podczas aktualizacji dashboardu: {e}")

@update_discord_dashboard.before_loop
async def before_update_discord_dashboard():
    await bot.wait_until_ready()


# --- Pętla sprawdzająca RSS ---
RSS_STATE_FILE = os.path.join(DATA_DIR, "rss_state.json")

def get_last_post_timestamp():
    """Odczytuje timestamp ostatniego posta z pliku stanu."""
    try:
        with open(RSS_STATE_FILE, 'r') as f:
            data = json.load(f)
            return data.get("last_post_timestamp", 0)
    except (FileNotFoundError, json.JSONDecodeError):
        return 0

def set_last_post_timestamp(timestamp):
    """Zapisuje timestamp ostatniego posta do pliku stanu."""
    with open(RSS_STATE_FILE, 'w') as f:
        json.dump({"last_post_timestamp": timestamp}, f)

@tasks.loop()
async def check_rss_feed():
    """Sprawdza kanał RSS i publikuje nowe artykuły."""
    try:
        with open(os.path.join(DATA_DIR, 'config.json'), 'r', encoding='utf-8') as f:
            config = json.load(f)
        rss_config = config.get("RSS_FEED")
        if not rss_config or not rss_config.get("URL") or not rss_config.get("CHANNEL_ID"):
            print("Ostrzeżenie: Konfiguracja RSS jest niekompletna lub jej brakuje w config.json. Zatrzymuję zadanie RSS.")
            check_rss_feed.stop()
            return
    except (FileNotFoundError, json.JSONDecodeError):
        print("Ostrzeżenie: Nie można załadować config.json dla zadania RSS. Zatrzymuję zadanie.")
        check_rss_feed.stop()
        return

    # Dynamiczne ustawienie interwału pętli
    interval = rss_config.get("INTERVAL_MINUTES", 15)
    if check_rss_feed.minutes != interval:
        check_rss_feed.change_interval(minutes=interval)
        print(f"Zmieniono interwał sprawdzania RSS na {interval} minut.")

    feed_url = rss_config["URL"]
    channel_id = rss_config["CHANNEL_ID"]
    channel = bot.get_channel(channel_id)

    if not channel:
        print(f"Błąd RSS: Nie znaleziono kanału o ID {channel_id}. Sprawdź konfigurację.")
        return

    print(f"Sprawdzam kanał RSS: {feed_url}")
    feed = await bot.loop.run_in_executor(None, feedparser.parse, feed_url)

    if feed.bozo:
        print(f"Błąd RSS: Nie udało się poprawnie sparsować kanału. Błąd: {feed.bozo_exception}")
        return

    last_post_timestamp = get_last_post_timestamp()
    new_entries = []

    for entry in feed.entries:
        published_time = time.mktime(entry.published_parsed)
        if published_time > last_post_timestamp:
            new_entries.append(entry)

    if not new_entries:
        print("RSS: Nie znaleziono nowych artykułów.")
        return

    # Sortuj od najstarszego do najnowszego, aby publikować w dobrej kolejności
    new_entries.sort(key=lambda e: e.published_parsed)
    
    # Jeśli to pierwsze uruchomienie, opublikuj tylko najnowszy artykuł
    is_first_run = last_post_timestamp == 0
    if is_first_run and new_entries:
        print("RSS: Pierwsze uruchomienie. Publikuję tylko najnowszy artykuł, aby uniknąć spamu.")
        new_entries = [new_entries[-1]]


    print(f"RSS: Znaleziono {len(new_entries)} nowych artykułów. Publikowanie...")
    latest_timestamp = last_post_timestamp
    for entry in new_entries:
        try:
            entry_timestamp = time.mktime(entry.published_parsed)
            
            # Wyciągnij obrazek, jeśli istnieje
            image_url = None
            if 'media_content' in entry and entry.media_content:
                image_url = entry.media_content[0]['url']
            elif 'links' in entry:
                for link in entry.links:
                    if link.get('type', '').startswith('image/'):
                        image_url = link.href
                        break

            embed = discord.Embed(
                title=entry.title,
                url=entry.link,
                description=entry.summary,
                color=discord.Color.blue(),
                timestamp=datetime.fromtimestamp(entry_timestamp)
            )
            if image_url:
                embed.set_image(url=image_url)
            embed.set_footer(text=f"Źródło: {feed.feed.title}")

            await channel.send(embed=embed)
            
            if entry_timestamp > latest_timestamp:
                latest_timestamp = entry_timestamp

        except Exception as e:
            print(f"Błąd podczas wysyłania artykułu RSS '{entry.title}': {e}")

    set_last_post_timestamp(latest_timestamp)
    print("RSS: Zakończono publikowanie nowych artykułów.")


@check_rss_feed.before_loop
async def before_check_rss_feed():
    await bot.wait_until_ready()


@bot.event
async def on_ready():
    """Wywoływane, gdy bot jest gotowy do pracy."""
    print(f'Zalogowano jako {bot.user} (ID: {bot.user.id})')
    print("Synchronizowanie listy członków serwera...")
    for guild in bot.guilds:
        await guild.chunk()
    print("Synchronizacja członków zakończona.")
    print('------')
    
    # Uruchomienie pętli w tle
    if not rotate_status.is_running():
        rotate_status.start()
        print("Uruchomiono pętlę rotacji statusu.")
        
    if not update_status_file.is_running():
        update_status_file.start()
        print("Uruchomiono pętlę zapisu statusu do pliku.")

    if not update_dashboard_data.is_running():
        update_dashboard_data.start()
        print("Uruchomiono pętlę zapisu danych dla panelu.")

    if not check_panel_commands.is_running():
        check_panel_commands.start()
        print("Uruchomiono pętlę sprawdzania komend z panelu.")

    if not update_discord_dashboard.is_running():
        update_discord_dashboard.start()
        print("Uruchomiono pętlę dashboardu na Discordzie.")

    if not check_rss_feed.is_running():
        check_rss_feed.start()
        print("Uruchomiono pętlę sprawdzania RSS.")
        
    # Uruchomienie pętli do obsługi wejścia z konsoli
    bot.loop.create_task(console_input_loop())
    print("Uruchomiono pętlę obsługi konsoli.")

    # Sprawdź zapisane blokady po restarcie
    await check_persistent_locks(bot)

    # --- Logika odblokowania kanałów przy starcie ---
    try:
        def read_config():
            with open(os.path.join(DATA_DIR, 'config.json'), 'r', encoding='utf-8') as f:
                return json.load(f)
        
        config_data = await bot.loop.run_in_executor(None, read_config)
        status_channel_id = config_data.get("STATUS_CHANNEL_ID")
        if status_channel_id:
            channel = bot.get_channel(int(status_channel_id))
            if channel:
                await channel.edit(name="🟢 Bot Online")
                print(f"Zaktualizowano nazwę kanału statusu na '🟢 Bot Online'.")
    except Exception as e:
        print(f"Nie udało się odczytać lub zaktualizować kanału statusu przy starcie: {e}")

    # Odblokowanie kanałów-hubów
    try:
        def read_servers_config():
            with open(os.path.join(DATA_DIR, 'servers.json'), 'r', encoding='utf-8') as f:
                return json.load(f)
        
        loaded_configs = await bot.loop.run_in_executor(None, read_servers_config)
        server_configs.update(loaded_configs)
    except (FileNotFoundError, json.JSONDecodeError):
        pass # Keep the existing (potentially empty) config

    for guild in bot.guilds:
        guild_id = str(guild.id)
        
        # Używamy globalnego configu jako fallback, jeśli serwera nie ma w pliku servers.json
        server_config = server_configs.get(guild_id, config)

        hub_channel_id = server_config.get("HUB_CHANNEL_ID")
        member_role_id = server_config.get("MEMBER_ROLE_ID")

        if not hub_channel_id:
            print(f"Ostrzeżenie: Brak HUB_CHANNEL_ID w konfiguracji dla serwera {guild.name} ({guild_id}).")
            continue

        try:
            print(f"DEBUG: Sprawdzam kanał-hub o ID: {hub_channel_id} dla serwera {guild.name}")
            hub_channel = bot.get_channel(hub_channel_id)
            if hub_channel:
                print("DEBUG: Kanał-hub znaleziony. Przystępuję do zmiany uprawnień.")
                
                member_role = guild.get_role(member_role_id) if member_role_id else None
                
                # Rola @everyone
                everyone_role = guild.default_role

                # Pobierz aktualne nadpisania uprawnień, aby ich nie kasować
                overwrites = hub_channel.overwrites

                # Ustaw uprawnienie 'Połącz' na TAK dla @everyone
                everyone_overwrite = overwrites.get(everyone_role, discord.PermissionOverwrite())
                everyone_overwrite.connect = True
                overwrites[everyone_role] = everyone_overwrite

                # Ustaw uprawnienie 'Połącz' na TAK dla roli podanej przez użytkownika
                if member_role:
                    member_overwrite = overwrites.get(member_role, discord.PermissionOverwrite())
                    member_overwrite.connect = True
                    overwrites[member_role] = member_overwrite
                    print(f"Przyznaję uprawnienia do łączenia dla roli: '{member_role.name}'")
                elif member_role_id:
                    print(f"OSTRZEŻENIE: Nie znaleziono roli o ID {member_role_id} na serwerze {guild.name}. Sprawdź, czy ID jest poprawne.")

                # Zastosuj wszystkie zmiany naraz
                await hub_channel.edit(name="➕ Stwórz kanał", overwrites=overwrites)
                print(f"Odblokowano kanał-hub 'Stwórz kanał' na serwerze {guild.name}.")
            else:
                print(f"DEBUG: NIE ZNALEZIONO kanału-hub o ID {hub_channel_id} na serwerze {guild.name}. Bot mógł jeszcze nie załadować wszystkich kanałów lub ID jest nieprawidłowe.")
        except Exception as e:
            print(f"Nie udało się odblokować kanału-hubu na serwerze {guild.name}: {e}")
        
    print("Bot jest w pełni gotowy do pracy.")
    await load_sounds()
    load_trivia_questions()


@bot.event
async def on_shutdown():
    print("DEBUG: Rozpoczęto on_shutdown.")
    # --- Aktualizacja i blokada kanałów-hubów przy wyłączaniu ---
    for guild in bot.guilds:
        guild_id = str(guild.id)
        if guild_id not in server_configs:
            continue

        server_config = server_configs[guild_id]
        hub_channel_id = server_config.get("HUB_CHANNEL_ID")

        if not hub_channel_id:
            continue

        try:
            hub_channel = bot.get_channel(hub_channel_id)
            if hub_channel:
                everyone_role = guild.default_role
                
                current_overwrites = hub_channel.overwrites
                everyone_overwrite = current_overwrites.get(everyone_role, discord.PermissionOverwrite())
                everyone_overwrite.connect = False
                current_overwrites[everyone_role] = everyone_overwrite

                print(f"DEBUG: Próba edycji kanału-hubu na serwerze {guild.name}...")
                await asyncio.wait_for(
                    hub_channel.edit(name="🔴 Kanał Offline", overwrites=current_overwrites),
                    timeout=10.0
                )
                print(f"Zmieniono nazwę i zablokowano kanał-hub na serwerze {guild.name}.")
            else:
                print(f"DEBUG: Nie znaleziono kanału-hubu podczas zamykania na serwerze {guild.name}.")
        except asyncio.TimeoutError:
            print(f"OSTRZEŻENIE: Edycja kanału-hubu na serwerze {guild.name} przekroczyła limit czasu podczas zamykania.")
        except Exception as e:
            print(f"Nie udało się zmienić nazwy i zablokować kanału-hubu przy wyłączaniu na serwerze {guild.name}: {e}")
    
    print("DEBUG: Zakończono on_shutdown.")

@bot.event
async def on_message(message):
    # Ignoruj wiadomości od samego bota oraz wiadomości prywatne
    if message.author.bot or not message.guild:
        return

    guild_id = str(message.guild.id)
    # Jeśli nie ma specyficznej konfiguracji dla serwera, używamy globalnej z config.json
    server_config = server_configs.get(guild_id, config)
    
    mod_log_channel_id = server_config.get("MOD_LOG_CHANNEL_ID")
    log_channel = bot.get_channel(mod_log_channel_id) if mod_log_channel_id else None

    # --- Anti-Spam Logic ---
    if ANTI_SPAM_CONFIG.get("ENABLED", False) and message.author.id != bot.owner_id:
        # Ignore users with manage_messages permission
        if not message.channel.permissions_for(message.author).manage_messages:
            current_time = time.time()
            user_id = message.author.id
            
            # Get user's message timestamps, or create a new list
            timestamps = user_message_timestamps.get(user_id, [])
            
            # Filter out old timestamps
            time_window = ANTI_SPAM_CONFIG.get("TIME_SECONDS", 5)
            timestamps = [t for t in timestamps if current_time - t < time_window]
            
            # Add the new timestamp
            timestamps.append(current_time)
            user_message_timestamps[user_id] = timestamps
            
            # Check if the user has exceeded the message count
            message_limit = ANTI_SPAM_CONFIG.get("MESSAGE_COUNT", 5)
            if len(timestamps) >= message_limit:
                # Mute the user
                punishment_config = ANTI_SPAM_CONFIG.get("PUNISHMENT", {})
                duration_str = punishment_config.get("DURATION", "5m")
                reason = punishment_config.get("REASON", "Automatycznie: Wykryto spam.")
                
                # Parse duration
                duration_seconds = 0
                unit = duration_str[-1].lower()
                try:
                    value = int(duration_str[:-1])
                    if unit == 's': duration_seconds = value
                    elif unit == 'm': duration_seconds = value * 60
                    elif unit == 'h': duration_seconds = value * 3600
                    elif unit == 'd': duration_seconds = value * 86400
                except (ValueError, TypeError):
                    duration_seconds = 300 # Default to 5 minutes on error
                
                try:
                    # Delete the spamming messages first
                    try:
                        await message.channel.purge(limit=message_limit, check=lambda m: m.author.id == user_id)
                    except discord.Forbidden:
                        if log_channel:
                            await log_channel.send(f":warning: Bot nie ma uprawnień do usuwania wiadomości na kanale {message.channel.mention}.")
                    
                    # Mute the user
                    await message.author.timeout(timedelta(seconds=duration_seconds), reason=reason)
                    
                    # Send DM to user
                    try:
                        dm_embed = discord.Embed(
                            title="Zostałeś tymczasowo wyciszony!",
                            description=f"Zostałeś automatycznie wyciszony na serwerze **{message.guild.name}** za spamowanie.",
                            color=discord.Color.red()
                        )
                        dm_embed.add_field(name="Czas trwania", value=duration_str, inline=False)
                        dm_embed.add_field(name="Powód", value=reason, inline=False)
                        await message.author.send(embed=dm_embed)
                    except discord.Forbidden:
                        pass # Can't send DMs

                    # Send log to mod channel
                    if log_channel:
                        log_embed = discord.Embed(
                            title="🚨 Wykryto Spam",
                            description=f"Użytkownik {message.author.mention} został automatycznie wyciszony za spam.",
                            color=discord.Color.red(),
                            timestamp=datetime.utcnow()
                        )
                        log_embed.add_field(name="Czas trwania", value=duration_str, inline=True)
                        log_embed.add_field(name="Kanał", value=message.channel.mention, inline=True)
                        log_embed.set_footer(text=f"ID Użytkownika: {user_id}")
                        await log_channel.send(embed=log_embed)
                        
                    # Clear the user's timestamps
                    user_message_timestamps[user_id] = []
                    
                    return # Stop processing the message
                except discord.Forbidden:
                    if log_channel:
                        await log_channel.send(f":warning: Nie udało się wyciszyć {message.author.mention}. Bot nie ma wystarczających uprawnień.")
                except Exception as e:
                    print(f"Błąd podczas wyciszania za spam: {e}")

    # --- Logika Filtra Zakazanych Słów ---
    # Na kanale o ID 1375587690765881465 filtr jest wyłączony.
    if message.channel.id != 1375587690765881465:
        content_lower = message.content.lower()
        detected_word = None
        for word in FORBIDDEN_WORDS:
            # Użyj regex z granicami słów (\b), aby uniknąć fałszywych trafień wewnątrz innych słów
            if re.search(r'\b' + re.escape(word) + r'\b', content_lower):
                detected_word = word
                break

        if detected_word:
            # 1. Wyślij log dla moderatorów
            if log_channel:
                log_embed = discord.Embed(
                    title="🚫 Wykryto Zakazane Słowo",
                    description=f"**Autor:** {message.author.mention} (`{message.author.id}`)\n**Kanał:** {message.channel.mention}",
                    color=discord.Color.orange(),
                    timestamp=datetime.utcnow()
                )
                log_embed.add_field(name="Pełna treść wiadomości", value=f"```{discord.utils.escape_markdown(message.content)}```", inline=False)
                await log_channel.send(embed=log_embed)

            # 2. Wyślij DM do użytkownika
            try:
                dm_embed = discord.Embed(
                    title="Twoja wiadomość została usunięta",
                    description=f"Twoja wiadomość na serwerze **{message.guild.name}** została automatycznie usunięta, ponieważ zawierała zakazane słowo. Otrzymałeś/aś za to automatyczne ostrzeżenie.",
                    color=discord.Color.red()
                )
                await message.author.send(embed=dm_embed)
            except discord.Forbidden:
                print(f"Nie udało się wysłać DM do {message.author.name} (prawdopodobnie ma zablokowane DM).")

            # 3. Usuń wiadomość
            try:
                await message.delete()
            except discord.NotFound:
                pass # Wiadomość mogła zostać już usunięta
            except discord.Forbidden:
                if log_channel:
                    await log_channel.send(":warning: Bot nie ma uprawnień do usuwania wiadomości na tym kanale.")

            # 4. Nadaj automatyczną karę
            kara_command = bot.get_command('kara')
            if kara_command:
                try:
                    # Tworzymy sztuczny kontekst, aby wywołać komendę
                    ctx = await bot.get_context(message)
                    await kara_command(ctx, strefa="żółta", member=message.author, odwolanie="NIE", mute_duration=None, reason="Automatycznie: Wykryto zakazane słowo")
                    if log_channel:
                        await log_channel.send(f"✅ Automatycznie nadano karę (Strefa Żółta) dla {message.author.mention}.")
                except Exception as e:
                    print(f"Błąd podczas automatycznego nadawania kary: {e}")
                    if log_channel:
                        await log_channel.send(f":warning: Wystąpił błąd podczas automatycznego nadawania kary dla {message.author.mention}: `{e}`")
            
            return # Zatrzymaj dalsze przetwarzanie wiadomości (np. komend)

    # Sprawdź, czy wiadomość to wzmianka bota i czy ma on odpowiadać
    if gemini_model and bot.user.mentioned_in(message) and not message.reference:
        async with message.channel.typing():
            prompt = message.content.replace(f'<@!{bot.user.id}>', '').replace(f'<@{bot.user.id}>', '').strip()
            
            if not prompt:
                return

            # Sprawdź, czy autorem wiadomości jest właściciel bota
            is_owner = message.author.id == bot.owner_id

            # Przygotuj instrukcję systemową dla AI
            system_instruction = (
                "Jesteś pomocnym asystentem AI. Odpowiadaj na pytania użytkowników. "
                "Jeśli użytkownik prosi o wykonanie komendy bota, odpowiedz w formacie: "
                "`COMMAND: <nazwa_komendy> ARGS: <argumenty>`. "
                "Dostępne komendy to: `play`, `kara`. "
                "Dla komendy `kara` wymagane są argumenty: `strefa` (np. zielona, żółta, czerwona), `użytkownik` (wzmianka lub ID), `odwołanie` (TAK/NIE), `powód`. "
                "Na przykład, jeśli użytkownik powie 'Zagraj piosenkę X', odpowiedz `COMMAND: play ARGS: piosenka X`. "
                "Jeśli użytkownik powie 'Ukarz @user za spam w strefie żółtej, bez odwołania', odpowiedz `COMMAND: kara ARGS: żółta @user NIE spam`. "
                "Jeśli użytkownik powie 'Ukarz użytkownika o ID 123456789012345678 za spam w strefie żółtej, bez odwołania', odpowiedz `COMMAND: kara ARGS: żółta 123456789012345678 NIE spam`. "
                "Jeśli nie jesteś pewien, po prostu odpowiedz tekstowo."
            )
            if current_ai_personality:
                system_instruction += f" Twoja aktualna osobowość to: '{current_ai_personality}'."
            # Stwórz finalny prompt
            final_prompt = f"Jesteś botem na Discordzie. {system_instruction} Odpowiedz na poniższą wiadomość użytkownika.\n\nWiadomość użytkownika: \"{prompt}\""

            print(f"Otrzymano prompt dla Gemini od {message.author.name}. Instrukcja: '{system_instruction}'.")
            
            try:
                response = await asyncio.to_thread(gemini_model.generate_content, final_prompt)
                if response and response.text:
                    print(f"Odpowiedź Gemini (raw): '{response.text}'") # New debug print
                    # Sprawdź, czy odpowiedź Gemini zawiera komendę do wykonania
                    command_match = re.match(r'COMMAND: (\w+) ARGS: (.*)', response.text, re.IGNORECASE)
                    if command_match:
                        print(f"Gemini zasugerowało komendę: {command_match.group(1)}, Argumenty: {command_match.group(2)}") # New debug print
                        command_name = command_match.group(1).lower()
                        command_args = command_match.group(2).strip()

                        # Lista dozwolonych komend, które AI może wywołać
                        allowed_commands = ['play', 'kara'] # Możesz dodać więcej komend tutaj

                        if command_name in allowed_commands:
                            print(f"Komenda '{command_name}' jest dozwolona.") # New debug print
                            # Utwórz kontekst dla komendy
                            fake_ctx = await bot.get_context(message)
                            
                            # Znajdź komendę
                            command = bot.get_command(command_name)
                            if command:
                                try:
                                    await message.reply(f"AI próbuje wykonać komendę: `{command_name} {command_args}`...")
                                    
                                    if command_name == 'kara':
                                        # Oczekiwany format: "strefa member odwolanie reason"
                                        parts = command_args.split(' ', 3) # Split into 4 parts: strefa, member, odwolanie, reason
                                        if len(parts) == 4:
                                            strefa_arg = parts[0]
                                            member_arg_str = parts[1] # Może być wzmianką lub ID
                                            odwolanie_arg = parts[2]
                                            reason_arg = parts[3]

                                            target_member = None
                                            member_id = None

                                            # Spróbuj sparsować jako wzmiankę
                                            mention_match = re.match(r'<@!?(\d+)>', member_arg_str)
                                            if mention_match:
                                                member_id = int(mention_match.group(1))
                                            else:
                                                # Spróbuj sparsować jako surowe ID
                                                try:
                                                    member_id = int(member_arg_str)
                                                except ValueError:
                                                    pass # Not a valid ID

                                            if member_id:
                                                try:
                                                    target_member = await fake_ctx.guild.fetch_member(member_id)
                                                except discord.NotFound:
                                                    target_member = None # Member not found in this guild
                                                except Exception as fetch_e:
                                                    print(f"Błąd podczas pobierania członka: {fetch_e}")
                                                    target_member = None
                                            
                                            if target_member:
                                                await command(fake_ctx, strefa_arg, target_member, odwolanie_arg, reason=reason_arg)
                                            else:
                                                await message.reply(f"AI zasugerowało komendę `kara`, ale nie mogłem znaleźć użytkownika: `{member_arg_str}`. Upewnij się, że użytkownik istnieje i jest na tym serwerze.")
                                        else:
                                            await message.reply(f"AI zasugerowało nieprawidłowy format argumentów dla komendy `kara`. Oczekiwano: `strefa użytkownik odwołanie powód`.")
                                    elif command_name == 'play':
                                        # 'play' przyjmuje argument 'search'
                                        await command(fake_ctx, search=command_args)
                                    else:
                                        # Domyślna obsługa dla innych komend, jeśli istnieją
                                        # To może wymagać dalszego dostosowania w zależności od sygnatur innych komend
                                        await command(fake_ctx, command_args)

                                except Exception as cmd_e:
                                    await message.reply(f"AI nie mogło wykonać komendy `{command_name}`: {cmd_e}")
                                    print(f"Błąd podczas wykonywania komendy przez AI: {cmd_e}")
                            else:
                                await message.reply(f"AI zasugerowało nieznaną komendę: `{command_name}`.")
                        else:
                            await message.reply(f"AI zasugerowało niedozwoloną komendę: `{command_name}`.")
                    else:
                        print(f"Odpowiedź Gemini nie zawiera komendy. Wysyłam tekst: '{response.text}'") # New debug print
                        # Jeśli odpowiedź nie jest komendą, po prostu ją wyślij
                        await message.reply(response.text)
            except Exception as e:
                print(f"Błąd podczas komunikacji z Gemini API: {e}")
                await message.reply("Wystąpił błąd podczas komunikacji z AI. Spróbuj ponownie później.")

    # --- Przetwarzanie komend i logiki gry ---
    global game_instance
    await bot.process_commands(message)

    if game_instance and game_instance.check_answer(message):
        winner = game_instance.winner
        scores[winner.id] = scores.get(winner.id, 0) + 1
        await message.channel.send(f'''🎉 Poprawna odpowiedź! **{winner.mention}** zdobywa punkt! 🎉
        Prawidłowa odpowiedź to: **{game_instance.song_info}**''')
        if message.guild.voice_client and message.guild.voice_client.is_playing():
            message.guild.voice_client.stop()
        game_instance = None
@bot.event
async def on_member_join(member):
    # --- Autorole ---
    try:
        with open(os.path.join(DATA_DIR, 'config.json'), 'r', encoding='utf-8') as f:
            config = json.load(f)
        
        autorole_config = config.get("AUTOROLE", {})
        user_role_id = autorole_config.get("USER_ROLE_ID")
        bot_role_id = autorole_config.get("BOT_ROLE_ID")

        if member.bot:
            if bot_role_id:
                role = member.guild.get_role(bot_role_id)
                if role:
                    await member.add_roles(role, reason="Automatyczne nadanie roli dla bota.")
                    print(f"Nadano rolę '{role.name}' dla bota {member.name}.")
                else:
                    print(f"Ostrzeżenie: Nie znaleziono roli dla bota o ID {bot_role_id}.")
        else:
            if user_role_id:
                role = member.guild.get_role(user_role_id)
                if role:
                    await member.add_roles(role, reason="Automatyczne nadanie roli dla użytkownika.")
                    print(f"Nadano rolę '{role.name}' dla użytkownika {member.name}.")
                else:
                    print(f"Ostrzeżenie: Nie znaleziono roli dla użytkownika o ID {user_role_id}.")

    except Exception as e:
        print(f"Błąd podczas nadawania roli automatycznej: {e}")


@bot.event
async def on_voice_state_update(member, before, after):
    """Zarządza tworzeniem i usuwaniem par kanałów tymczasowych (głosowy + tekstowy)."""
    global temp_channels

    if member.bot:
        return

    guild_id = str(member.guild.id)
    server_config = server_configs.get(guild_id, config)
    
    hub_channel_id = server_config.get("HUB_CHANNEL_ID")
    temp_channel_category_id = server_config.get("TEMP_CHANNEL_CATEGORY_ID")

    if not hub_channel_id or not temp_channel_category_id:
        return

    hub_channel = bot.get_channel(hub_channel_id)
    temp_category = bot.get_channel(temp_channel_category_id)

    if not hub_channel or not temp_category:
        return

    # --- Logika Tworzenia Kanałów ---
    if after.channel and after.channel.id == hub_channel_id:
        try:
            # --- Tworzenie kanału głosowego ---
            vc_name = f"Kanał {member.display_name}"
            vc_overwrites = {
                member: discord.PermissionOverwrite(manage_channels=True, manage_roles=True, move_members=True, view_channel=True),
                member.guild.default_role: discord.PermissionOverwrite(view_channel=True)
            }
            new_vc = await temp_category.create_voice_channel(name=vc_name, overwrites=vc_overwrites, reason=f"Utworzono na prośbę {member.name}")

            # --- Tworzenie kanału tekstowego ---
            tc_name = f"panel-{member.display_name}".lower()
            tc_overwrites = {
                member: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_messages=True),
                member.guild.default_role: discord.PermissionOverwrite(view_channel=False) # Ukryj przed resztą
            }
            new_tc = await temp_category.create_text_channel(name=tc_name, overwrites=tc_overwrites, reason=f"Panel dla kanału tymczasowego {new_vc.name}")

            # --- Przeniesienie użytkownika i zapisanie pary kanałów ---
            await member.move_to(new_vc)
            temp_channels[new_vc.id] = new_tc.id
            print(f"Utworzono parę kanałów tymczasowych: {new_vc.name} i {new_tc.name} dla {member.name}")

            # --- Wysłanie panelu sterowania ---
            embed = discord.Embed(
                title=f"Panel Zarządzania Kanałem",
                description=f"Witaj {member.mention}! To jest Twój prywatny panel do zarządzania kanałem głosowym **{new_vc.name}**.",
                color=discord.Color.blue()
            )
            embed.set_footer(text="Ten kanał tekstowy zostanie automatycznie usunięty wraz z kanałem głosowym.")
            await new_tc.send(embed=embed, view=TempChannelView(voice_channel=new_vc, text_channel=new_tc))

        except discord.Forbidden:
            print("Błąd: Bot nie ma uprawnień do tworzenia kanałów lub przenoszenia użytkowników.")
        except Exception as e:
            print(f"Wystąpił nieoczekiwany błąd podczas tworzenia kanałów: {e}")

    # --- Logika Usuwania Kanałów ---
    if before.channel and before.channel.id in temp_channels:
        if len(before.channel.members) == 0:
            try:
                # Znajdź i usuń sparowany kanał tekstowy
                text_channel_id = temp_channels.get(before.channel.id)
                if text_channel_id:
                    text_channel = bot.get_channel(text_channel_id)
                    if text_channel:
                        await text_channel.delete(reason="Kanał głosowy był pusty.")
                        print(f"Usunięto kanał tekstowy: {text_channel.name}")

                # Usuń kanał głosowy
                await before.channel.delete(reason="Kanał tymczasowy był pusty.")
                print(f"Usunięto kanał głosowy: {before.channel.name}")

                # Usuń wpis ze słownika
                del temp_channels[before.channel.id]

            except discord.NotFound:
                # Któryś z kanałów mógł już zostać usunięty
                if before.channel.id in temp_channels:
                    del temp_channels[before.channel.id]
            except discord.Forbidden:
                print(f"Błąd: Bot nie ma uprawnień do usunięcia kanału {before.channel.name} lub jego pary tekstowej.")
            except Exception as e:
                print(f"Wystąpił nieoczekiwany błąd podczas usuwania kanałów: {e}")


# --- Pętla do obsługi wejścia z konsoli ---
async def console_input_loop():
    """Nasłuchuje na wejście w konsoli i wysyła wiadomości na Discord."""
    await bot.wait_until_ready()

    # --- Odczyt ID kanału z config.json ---
    console_channel_id = None
    channel_key_name = "SEND_CHANNEL_ID" # Użyj klucza, który istnieje w pliku
    try:
        with open(os.path.join(DATA_DIR, 'config.json'), 'r', encoding='utf-8') as f:
            config = json.load(f)
            console_channel_id_str = config.get(channel_key_name)
            if console_channel_id_str:
                try:
                    console_channel_id = int(console_channel_id_str)
                except (ValueError, TypeError):
                    print(f"Ostrzeżenie: {channel_key_name} w config.json ('{console_channel_id_str}') nie jest prawidłową liczbą.")
                    console_channel_id = None
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f"Ostrzeżenie: Nie można odczytać config.json ({e}).")

    target_channel = None
    if console_channel_id:
        target_channel = bot.get_channel(console_channel_id)
        if target_channel and isinstance(target_channel, discord.TextChannel):
            print("\n----------------------------------------------------")
            print(f"Tryb konsoli aktywny.")
            print(f"Wiadomości będą wysyłane na kanał: #{target_channel.name} ({target_channel.id})")
            print("Wpisz wiadomość i naciśnij Enter.")
            print("----------------------------------------------------")
        else:
            print(f"BŁĄD: Kanał konsoli o ID {console_channel_id} (z klucza '{channel_key_name}') nie został znaleziony lub nie jest kanałem tekstowym.")
            target_channel = None # Unset if invalid
    
    if not target_channel:
        print("\n----------------------------------------------------")
        print("Tryb konsoli aktywny (tryb ręczny).")
        print(f"Nie znaleziono/nie udało się załadować kanału z pliku config.json.")
        print("Format wprowadzania: <ID_kanału> <wiadomość>")
        print("----------------------------------------------------")

    while not bot.is_closed():
        try:
            line = await asyncio.to_thread(sys.stdin.readline)
            if not line:
                print("Wykryto koniec wejścia (EOF), zamykanie pętli konsoli.")
                break
            
            message_to_send = line.strip()
            if not message_to_send:
                continue

            channel_for_message = target_channel
            
            # If a default channel isn't set, parse the input for ID and message
            if not channel_for_message:
                parts = message_to_send.split(' ', 1)
                if len(parts) == 2:
                    try:
                        manual_id = int(parts[0])
                        channel_for_message = bot.get_channel(manual_id)
                        message_to_send = parts[1]
                    except (ValueError, IndexError):
                        print(">>> BŁĄD: Nieprawidłowy format. Oczekiwano: <ID_kanału> <wiadomość>")
                        continue
                else:
                    print(">>> BŁĄD: Nie ustawiono domyślnego kanału i nie podano ID w komendzie.")
                    continue

            if channel_for_message and isinstance(channel_for_message, discord.TextChannel):
                try:
                    await channel_for_message.send(message_to_send)
                    print(f">>> Wiadomość wysłana pomyślnie na kanał #{channel_for_message.name}")
                except discord.Forbidden:
                    print(f">>> BŁĄD: Bot nie ma uprawnień do wysyłania wiadomości na kanale #{channel_for_message.name}.")
                except Exception as e:
                    print(f">>> BŁĄD: Nie udało się wysłać wiadomości: {e}")
            elif not channel_for_message:
                print(f">>> BŁĄD: Nie znaleziono kanału docelowego.")
            else:
                print(f">>> BŁĄD: Kanał docelowy nie jest kanałem tekstowym.")

        except Exception as e:
            print(f">>> KRYTYCZNY BŁĄD w pętli konsoli: {e}")
            break


@bot.command(name="stwórz-ogloszenie")
@commands.has_permissions(manage_messages=True)
async def create_embed(ctx):
    """Rozpoczyna interaktywny proces tworzenia wiadomości embed."""
    try:
        await ctx.message.delete()
    except (discord.Forbidden, discord.NotFound):
        pass

    def check(m):
        return m.author == ctx.author and m.channel == ctx.channel

    try:
        await ctx.send("Rozpoczynamy tworzenie ogłoszenia! Podaj, na którym kanale ma zostać wysłane (np. #nazwa-kanalu lub ID kanału).", delete_after=30)
        msg = await bot.wait_for('message', check=check, timeout=30.0)
        
        target_channel = None
        # Spróbuj znaleźć kanał przez wzmiankę
        if msg.channel_mentions:
            target_channel = msg.channel_mentions[0]
        else:
            # Spróbuj znaleźć kanał przez ID
            try:
                channel_id = int(msg.content)
                target_channel = bot.get_channel(channel_id)
            except ValueError:
                await ctx.send("Nieprawidłowy format. Musisz podać wzmiankę kanału (#nazwa) lub jego ID.", delete_after=10)
                return

        if not target_channel:
            await ctx.send(f"Nie znaleziono kanału. Spróbuj ponownie.", delete_after=10)
            return

        # --- Tytuł ---
        await ctx.send(f"Super! Ogłoszenie zostanie wysłane na {target_channel.mention}. Teraz podaj **tytuł** ogłoszenia.", delete_after=30)
        title_msg = await bot.wait_for('message', check=check, timeout=60.0)
        embed_title = title_msg.content

        # --- Treść ---
        await ctx.send("OK. Teraz podaj **treść** ogłoszenia. Możesz używać formatowania markdown.", delete_after=30)
        desc_msg = await bot.wait_for('message', check=check, timeout=300.0)
        embed_description = desc_msg.content

        # --- Kolor ---
        await ctx.send("Dobrze. Teraz podaj **kolor** ramki w formacie hex (np. `#FF5733` lub `0xFF5733`). Jeśli nie chcesz, wpisz `brak`.", delete_after=30)
        color_msg = await bot.wait_for('message', check=check, timeout=60.0)
        embed_color = discord.Color.default()
        if color_msg.content.lower() != 'brak':
            try:
                color_hex = color_msg.content.replace('#', '')
                embed_color = discord.Color(int(color_hex, 16))
            except ValueError:
                await ctx.send("Nieprawidłowy format koloru. Używam domyślnego.", delete_after=5)

        # --- Stopka ---
        await ctx.send("Prawie gotowe. Podaj tekst **stopki** (mały tekst na samym dole). Jeśli nie chcesz, wpisz `brak`.", delete_after=30)
        footer_msg = await bot.wait_for('message', check=check, timeout=60.0)
        embed_footer = None
        if footer_msg.content.lower() != 'brak':
            embed_footer = footer_msg.content

        # --- Podgląd i Potwierdzenie ---
        final_embed = discord.Embed(title=embed_title, description=embed_description, color=embed_color)
        if embed_footer:
            final_embed.set_footer(text=embed_footer)

        preview_msg = await ctx.send("Oto podgląd Twojego ogłoszenia. Czy chcesz je wysłać na kanał? (`TAK`/`NIE`)", embed=final_embed)
        
        try:
            confirm_msg = await bot.wait_for('message', check=check, timeout=60.0)
            if confirm_msg.content.lower() in ['tak', 't', 'yes', 'y']:
                await target_channel.send(embed=final_embed)
                await ctx.send(f"✅ Ogłoszenie zostało wysłane na {target_channel.mention}!", delete_after=10)
            else:
                await ctx.send("Anulowano wysyłanie ogłoszenia.", delete_after=10)
        except asyncio.TimeoutError:
            await ctx.send("Przekroczono czas na potwierdzenie. Anulowano.", delete_after=10)
        finally:
            # Sprzątanie wiadomości
            try:
                await ctx.channel.delete_messages([title_msg, desc_msg, color_msg, footer_msg, confirm_msg, preview_msg])
            except Exception as e:
                print(f"Błąd podczas sprzątania wiadomości w create_embed: {e}")

    except asyncio.TimeoutError:
        await ctx.send("Przekroczono czas na odpowiedź. Anulowano tworzenie ogłoszenia.", delete_after=10)
    except Exception as e:
        await ctx.send(f"Wystąpił nieoczekiwany błąd: {e}", delete_after=10)


if __name__ == "__main__":

    try:
        bot.run(BOT_TOKEN)
    except (KeyboardInterrupt, SystemExit):
        print("Otrzymano sygnał wyłączenia. Rozpoczynam procedurę zamykania...")
        asyncio.run(bot.close())