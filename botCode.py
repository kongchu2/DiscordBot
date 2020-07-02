import asyncio
import discord
import re
import requests
import random
import lxml
import itertools
import sys
import traceback
from async_timeout import timeout
from functools import partial
from bs4 import BeautifulSoup
from discord.ext import commands
from youtube_dl import YoutubeDL

ytdlopts = {
    'format': 'bestaudio/best',
    'outtmpl': 'downloads/%(extractor)s-%(id)s-%(title)s.%(ext)s',
    'restrictfilenames': True,
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0'  # ipv6 addresses cause issues sometimes
    }

ffmpegopts = {
    'before_options': '-nostdin',
    'options': '-vn'
    }

ytdl = YoutubeDL(ytdlopts)

class VoiceConnectionError(commands.CommandError):
    """Custom Exception class for connection errors."""

class InvalidVoiceChannel(VoiceConnectionError):
    """Exception for cases of invalid Voice Channels."""

class YTDLSource(discord.PCMVolumeTransformer):

    def __init__(self, source, *, data, requester):
        super().__init__(source)
        self.requester = requester

        self.title = data.get('title')
        self.web_url = data.get('webpage_url')

        # YTDL info dicts (data) have other useful information you might want
        # https://github.com/rg3/youtube-dl/blob/master/README.md

    def __getitem__(self, item: str):
        """Allows us to access attributes similar to a dict.
        This is only useful when you are NOT downloading.
        """
        return self.__getattribute__(item)

    @classmethod
    async def create_source(cls, ctx, search: str, *, loop, download=False):
        loop = loop or asyncio.get_event_loop()

        to_run = partial(ytdl.extract_info, url=search, download=download)
        data = await loop.run_in_executor(None, to_run)

        if 'entries' in data:
            # take first item from a playlist
            data = data['entries'][0]

        await ctx.send(f'```ini\n[Added {data["title"]} to the Queue.]\n```', delete_after=15)

        if download:
            source = ytdl.prepare_filename(data)
        else:
            return {'webpage_url': data['webpage_url'], 'requester': ctx.author, 'title': data['title']}

        return cls(discord.FFmpegPCMAudio(source), data=data, requester=ctx.author)

    @classmethod
    async def regather_stream(cls, data, *, loop):
        """Used for preparing a stream, instead of downloading.
        Since Youtube Streaming links expire."""
        loop = loop or asyncio.get_event_loop()
        requester = data['requester']

        to_run = partial(ytdl.extract_info, url=data['webpage_url'], download=False)
        data = await loop.run_in_executor(None, to_run)

        return cls(discord.FFmpegPCMAudio(data['url']), data=data, requester=requester)

class MusicPlayer(commands.Cog):
    """A class which is assigned to each guild using the bot for Music.
    This class implements a queue and loop, which allows for different guilds to listen to different playlists
    simultaneously.
    When the bot disconnects from the Voice it's instance will be destroyed.
    """

    __slots__ = ('bot', '_guild', '_channel', '_cog', 'queue', 'next', 'current', 'np', 'volume')

    def __init__(self, ctx):
        self.bot = ctx.bot
        self._guild = ctx.guild
        self._channel = ctx.channel
        self._cog = bot.get_cog('Music')

        self.queue = asyncio.Queue()
        self.next = asyncio.Event()

        self.np = None  # Now playing message
        self.volume = 0.5
        self.current = None

        ctx.bot.loop.create_task(self.player_loop())

    async def player_loop(self):
        """Our main player loop."""
        await self.bot.wait_until_ready()

        while not self.bot.is_closed():
            self.next.clear()

            try:
                # Wait for the next song. If we timeout cancel the player and disconnect...
                async with timeout(300):  # 5 minutes...
                    source = await self.queue.get()
            except asyncio.TimeoutError:
                return self.destroy(self._guild)

            if not isinstance(source, YTDLSource):
                # Source was probably a stream (not downloaded)
                # So we should regather to prevent stream expiration
                try:
                    source = await YTDLSource.regather_stream(source, loop=self.bot.loop)
                except Exception as e:
                    await self._channel.send(f'There was an error processing your song.\n'
                                             f'```css\n[{e}]\n```') 
                    continue

            source.volume = self.volume
            self.current = source

            self._guild.voice_client.play(source, after=lambda _: self.bot.loop.call_soon_threadsafe(self.next.set))
            self.np = await self._channel.send(f'**Now Playing:** `{source.title}` requested by '
                                               f'`{source.requester}`')
            await self.next.wait()

            # Make sure the FFmpeg process is cleaned up.
            source.cleanup()
            self.current = None

            try:
                # We are no longer playing this song...
                await self.np.delete()
            except discord.HTTPException:
                pass

    def destroy(self, guild):
        """Disconnect and cleanup the player."""
        return self.bot.loop.create_task(self._cog.cleanup(guild))

class Music(commands.Cog):
    """Music related commands."""

    __slots__ = ('bot', 'players')

    def __init__(self, bot):
        self.bot = bot
        self.players = {}

    async def cleanup(self, guild):
        try:
            await guild.voice_client.disconnect()
        except AttributeError:
            pass

        try:
            del self.players[guild.id]
        except KeyError:
            pass

    async def __local_check(self, ctx):
        """A local check which applies to all commands in this cog."""
        if not ctx.guild:
            raise commands.NoPrivateMessage
        return True

    async def __error(self, ctx, error):
        """A local error handler for all errors arising from commands in this cog."""
        if isinstance(error, commands.NoPrivateMessage):
            try:
                return await ctx.send('This command can not be used in Private Messages.')
            except discord.HTTPException:
                pass
        elif isinstance(error, InvalidVoiceChannel):
            await ctx.send('Error connecting to Voice Channel. '
                           'Please make sure you are in a valid channel or provide me with one')

        print('Ignoring exception in command {}:'.format(ctx.command), file=sys.stderr)
        traceback.print_exception(type(error), error, error.__traceback__, file=sys.stderr)

    def get_player(self, ctx):
        """Retrieve the guild player, or generate one."""
        try:
            player = self.players[ctx.guild.id]
        except KeyError:
            player = MusicPlayer(ctx)
            self.players[ctx.guild.id] = player

        return player

    @commands.command(name='connect', aliases=['join'])
    async def connect_(self, ctx, *, channel: discord.VoiceChannel=None):
        """Connect to voice.
        Parameters
        ------------
        channel: discord.VoiceChannel [Optional]
            The channel to connect to. If a channel is not specified, an attempt to join the voice channel you are in
            will be made.
        This command also handles moving the bot to different channels.
        """
        if not channel:
            try:
                channel = ctx.author.voice.channel
            except AttributeError:
                raise InvalidVoiceChannel('No channel to join. Please either specify a valid channel or join one.')

        vc = ctx.voice_client

        if vc:
            if vc.channel.id == channel.id:
                return
            try:
                await vc.move_to(channel)
            except asyncio.TimeoutError:
                raise VoiceConnectionError(f'Moving to channel: <{channel}> timed out.')
        else:
            try:
                await channel.connect()
            except asyncio.TimeoutError:
                raise VoiceConnectionError(f'Connecting to channel: <{channel}> timed out.')

        await ctx.send(f'Connected to: **{channel}**', delete_after=20)

    @commands.command(name='play', aliases=['sing'])
    async def play_(self, ctx, *, search: str):
        """Request a song and add it to the queue.
        This command attempts to join a valid voice channel if the bot is not already in one.
        Uses YTDL to automatically search and retrieve a song.
        Parameters
        ------------
        search: str [Required]
            The song to search and retrieve using YTDL. This could be a simple search, an ID or URL.
        """
        await ctx.trigger_typing()

        vc = ctx.voice_client

        if not vc:
            await ctx.invoke(self.connect_)

        player = self.get_player(ctx)

        # If download is False, source will be a dict which will be used later to regather the stream.
        # If download is True, source will be a discord.FFmpegPCMAudio with a VolumeTransformer.
        source = await YTDLSource.create_source(ctx, search, loop=self.bot.loop, download=False)
        await player.queue.put(source)

    @commands.command(name='pause')
    async def pause_(self, ctx):
        """Pause the currently playing song."""
        vc = ctx.voice_client

        if not vc or not vc.is_playing():
            return await ctx.send('I am not currently playing anything!', delete_after=20)
        elif vc.is_paused():
            return

        vc.pause()
        await ctx.send(f'**`{ctx.author}`**: Paused the song!')

    @commands.command(name='resume')
    async def resume_(self, ctx):
        """Resume the currently paused song."""
        vc = ctx.voice_client

        if not vc or not vc.is_connected():
            return await ctx.send('I am not currently playing anything!', delete_after=20)
        elif not vc.is_paused():
            return

        vc.resume()
        await ctx.send(f'**`{ctx.author}`**: Resumed the song!')

    @commands.command(name='skip')
    async def skip_(self, ctx):
        """Skip the song."""
        vc = ctx.voice_client

        if not vc or not vc.is_connected():
            return await ctx.send('I am not currently playing anything!', delete_after=20)

        if vc.is_paused():
            pass
        elif not vc.is_playing():
            return

        vc.stop()
        await ctx.send(f'**`{ctx.author}`**: Skipped the song!')

    @commands.command(name='queue', aliases=['q', 'playlist'])
    async def queue_info(self, ctx):
        """Retrieve a basic queue of upcoming songs."""
        vc = ctx.voice_client

        if not vc or not vc.is_connected():
            return await ctx.send('I am not currently connected to voice!', delete_after=20)

        player = self.get_player(ctx)
        if player.queue.empty():
            return await ctx.send('There are currently no more queued songs.')

        # Grab up to 5 entries from the queue...
        upcoming = list(itertools.islice(player.queue._queue, 0, 5))

        fmt = '\n'.join(f'**`{_["title"]}`**' for _ in upcoming)
        embed = discord.Embed(title=f'Upcoming - Next {len(upcoming)}', description=fmt)

        await ctx.send(embed=embed)

    @commands.command(name='now_playing', aliases=['np', 'current', 'currentsong', 'playing'])
    async def now_playing_(self, ctx):
        """Display information about the currently playing song."""
        vc = ctx.voice_client

        if not vc or not vc.is_connected():
            return await ctx.send('I am not currently connected to voice!', delete_after=20)

        player = self.get_player(ctx)
        if not player.current:
            return await ctx.send('I am not currently playing anything!')

        try:
            # Remove our previous now_playing message.
            await player.np.delete()
        except discord.HTTPException:
            pass

        player.np = await ctx.send(f'**Now Playing:** `{vc.source.title}` '
                                   f'requested by `{vc.source.requester}`')

    @commands.command(name='volume', aliases=['vol'])
    async def change_volume(self, ctx, *, vol: float):
        """Change the player volume.
        Parameters
        ------------
        volume: float or int [Required]
            The volume to set the player to in percentage. This must be between 1 and 100.
        """
        vc = ctx.voice_client

        if not vc or not vc.is_connected():
            return await ctx.send('I am not currently connected to voice!', delete_after=20)

        if not 0 < vol < 101:
            return await ctx.send('Please enter a value between 1 and 100.')

        player = self.get_player(ctx)

        if vc.source:
            vc.source.volume = vol / 100

        player.volume = vol / 100
        await ctx.send(f'**`{ctx.author}`**: Set the volume to **{vol}%**')

    @commands.command(name='stop')
    async def stop_(self, ctx):
        """Stop the currently playing song and destroy the player.
        !Warning!
            This will destroy the player assigned to your guild, also deleting any queued songs and settings.
        """
        vc = ctx.voice_client

        #if not vc or not vc.is_connected():
            #return await ctx.send('I am not currently playing anything!', delete_after=20)

        await self.cleanup(ctx.guild)
#playlist 재생

bot = commands.Bot(command_prefix='!', description='신카이 마코토')
bot.add_cog(Music(bot))
Cog = bot.get_cog('Music')
commands = Cog.get_commands()
learnList = []
votedDic = {}
searched = []

@bot.event
async def on_ready():
    status_List = []
    print(bot.user.name)
    print(bot.user.id)
    while True:
        await bot.change_presence(status=discord.Status.online, activity=discord.Game(random.choice(status_List)))
        await asyncio.sleep(600)

@bot.event
async def on_message(message):
    if message.author.bot:
        return None
    messagesendname = message.author.name
    channel = message.channel
    ctx = await bot.get_context(message)                                                                                                                                   
    if message.content.startswith('!명령어') or message.content.startswith('!help'):
        embed = discord.Embed(title = '명령어들이에옹', color = discord.Colour.blue())
        embed.add_field(name = '!검색', value='\" !검색 <keyword> \" 형식으로 적으면 유튜브에 검색함', inline=False)
        embed.add_field(name = '!동전', value='동전 던져줌', inline=False)
        embed.add_field(name = '!주사위', value='주사위 굴려줌', inline=False)
        embed.add_field(name = '!타이머', value='\" !타이머 <time> \" 형식으로 적으면 타이머 시작됨')
        embed.add_field(name = '!리스트 추가', value='\" !리스트 추가 <keyword> \" 형식으로 적으면 리스트에 <keyword>를 추가함', inline=False)
        embed.add_field(name = '!리스트 제거', value='\" !리스트 제거 <keyword> \" 형식으로 적으면 리스트에 있는 <keyword>를 제거함', inline=False)
        embed.add_field(name = '!리스트 확인', value='리스트 목록을 확인함', inline=False)
        embed.add_field(name = '!리스트 뽑기', value='리스트 중에서 하나를 골라 뽑음', inline=False)
        embed.add_field(name = '!리스트 초기화', value='리스트를 초기화함', inline=False)
        embed.add_field(name = '!투표', value='!투표 <keyword> 리스트에 들어있는 키워드를 투표 할 수 있음', inline=False)
        embed.add_field(name = '!투표 확인', value='투표 현황을 확인 가능함', inline=False)
        embed.add_field(name = '!투표 초기화', value='투표를 초기화함.', inline=False)
        embed.add_field(name = '!롤', value='\" !롤 <nickname> \"형식으로 적으면 전적검색 함', inline=False)
        embed.add_field(name = '!멀티서치', value='\" !롤 <nickname1>, <nickname2>... \"형식으로 적으면 멀티서치 함', inline=False)
        embed.add_field(name = '!포지션 #티어', value='\" !<position> <tier>티어 \"형식으로 적으면 <tier>별 챔피언 알려줌', inline=False)
        embed.add_field(name = '!챔피언이름 룬', value='\" !<champion> 룬 \"형식으로 적으면 룬 알려줌', inline=False)
        embed.add_field(name = '!챔피언이름 시작템', value='\" !<champion> 시작템 \"형식으로 적으면 시작아이템 알려줌', inline=False)
        embed.add_field(name = '!챔피언이름 템', value='\" !<champion> 템 \"형식으로 적으면 픽률 기준 추천빌드 알려줌 (3개)', inline=False)
        embed.add_field(name = '!느낌', value='\" !<name>느낌 \"형식으로 적으면 느낌 알려줌', inline=False)
        embed.add_field(name = '!재생', value='\" !재생 <youtube url OR search_number> \" 형식으로 적으면 유튜브 영상 틀어줌', inline=False)
        embed.add_field(name = '!pause', value='영상 일시정지', inline=False)
        embed.add_field(name = '!resume', value='영상 다시 재생', inline=False)
        embed.add_field(name = '!skip', value='영상 스킵', inline=False)
        embed.add_field(name = '!nowplaying', value='현재 재생중인 영상 정보', inline=False)
        embed.add_field(name = '!stop', value='영상 재생 종료', inline=False)
        embed.add_field(name = '!queueinfo', value='영상 큐 정보', inline=False)
        embed.add_field(name = '!섯다', value='\" !섯다 <count> \"형식으로 적으면 섯다 패<count> 개수만큼 핌', inline=False)
        embed.add_field(name = '!번역', value='\" !번역 <문장> \"형식으로 적으면 번역해줌', inline=False)
        await channel.send(embed=embed)
    elif message.content.startswith('!롤'):
        search_name = message.content.replace('!롤', '').strip()
        if search_name:
            if '님이 로비에 참가하셨습니다.' in search_name:
                search_name = search_name.split('님이 로비에 참가하셨습니다.')
                for index in range(len(search_name)):
                    if search_name[index] == '':
                        del search_name[index]
                    else:
                        search_name[index] = search_name[index].replace('\n', '')
                for sname in search_name:
                    await Summonerinfo(sname, channel)
            else:
                await Summonerinfo(search_name, channel)
        else:
            await channel.send('소환사명을 입력해 주세요.')
    elif message.content.startswith('!멀티서치'):
        url = "https://www.op.gg/multi/query="
        search_names = message.content.replace('!멀티서치', '').strip()
        if '님이 로비에 참가하셨습니다.' in search_names:
            search_names = search_names.split('님이 로비에 참가하셨습니다.')
        elif ',' in search_names:
            search_names = search_names.split(',')
        for name in search_names:
            url = url + name.strip() + "%2C"
        await channel.send(url)
    elif re.fullmatch('^!시작템\s[가-힣]{1,10}', message.content) or re.fullmatch('^![가-힣]{1,10}\s시작템', message.content):#첫템검색
        champ_kor = message.content.replace('!', '').replace('시작템','').replace(' ', '')
        champ_eng = translate_champion(champ_kor)
        if not champ_eng:
            await channel.send('알 수 없는 챔피언명 입니다')
            return None     
        url = 'https://www.op.gg/champion/'+champ_eng+'/statistics/item'
        soup = getBSoup(url)
        position = soup.select_one('span.champion-stats-header__position__role').text.strip()
        first_item = soup.find(text='시작 아이템')
        item_box = first_item.find_parent('table')
        tbody = item_box.find('tbody')
        item = tbody.find_all('tr', limit=2, recursive=False)
        item_1 = item[0].find_all('img')
        item_2 = item[1].find_all('img')
        for index in range(len(item_1)):
            item_1[index] = item_1[index].get('src').split(',')[0]
        for index in range(len(item_2)):
            item_2[index] = item_2[index].get('src').split(',')[0]
        await channel.send(embed=discord.Embed(title=position + ' ' + champ_kor + ' 시작 아이템', colour=discord.Color.blue()))
        for item in item_1:
            await channel.send(embed=discord.Embed(type='image', colour=discord.Color.blue()).set_image(url='https:' + item))
        await channel.send('OR')
        for item in item_2:
            await channel.send(embed=discord.Embed(type='image', colour=discord.Color.blue()).set_image(url='https:' + item))
    elif re.fullmatch('^!템\s[가-힣]{1,10}',message.content) or re.fullmatch('^![가-힣]{1,10}\s템', message.content):#템검색
        champ_kor = message.content.replace('!', '').replace('템', '').replace(' ', '')
        champ_eng = translate_champion(champ_kor)
        if not champ_eng:
            await channel.send('알 수 없는 챔피언명 입니다')
            return None
        url = 'https://www.op.gg/champion/'+champ_eng+'/statistics'
        soup = getBSoup(url)
        position = soup.select_one('span.champion-stats-header__position__role').text.strip()
        itemtable = soup.find_all('table', {'class':'champion-overview__table'},  limit=2)[1]
        items = itemtable.find_all('tr', {'class':'champion-overview__row'}, limit=3)[2]
        item_imgs = items.find_all('img')
        await channel.send(embed=discord.Embed(title=position + ' ' + champ_kor + ' 아이템', colour=discord.Color.blue()))
        for img in item_imgs:
            img = 'https:' + img.get('src')
            if 'blet' in img:
                continue
            await channel.send(embed=discord.Embed(type='image', colour=discord.Color.blue()).set_image(url=img))
    elif re.fullmatch('^!룬\s[가-힣]{1,10}', message.content) or re.fullmatch('^![가-힣]{1,10}\s룬', message.content):#룬검색
        champ_kor = message.content.replace('!', '').replace('룬','').replace(' ', '')
        champ_eng = translate_champion(champ_kor)
        if not champ_eng:
            await channel.send('알 수 없는 챔피언명 입니다.')
            return None
        url = 'https://www.op.gg/champion/'+champ_eng+'/statistics/rune'
        soup = getBSoup(url)
        position = soup.select_one('li.champion-stats-header__position.champion-stats-header__position--active > a > span.champion-stats-header__position__role').text.strip()
        Runes = soup.find_all('div', {'class':'perk-page__item--active'}, limit=6)
        Main_Rune_name = Runes[0].img.get('alt').replace(':', '')
        Top_Rune_name = Runes[1].img.get('alt')
        Mid_Rune_name = Runes[2].img.get('alt').replace(':', '')
        Bot_Rune_name = Runes[3].img.get('alt')
        Sub_Top_Rune = Runes[4].img.get('alt').replace(':', '')
        Sub_Bot_Rune = Runes[5].img.get('alt').replace(':', '')
        path = 'rune/'
        await channel.send(position+' '+champ_kor+' 룬')
        await channel.send(file=discord.File(path+Main_Rune_name+'.png'))
        await channel.send(file=discord.File(path+Top_Rune_name+'.png'))
        await channel.send(file=discord.File(path+Mid_Rune_name+'.png'))
        await channel.send(file=discord.File(path+Bot_Rune_name+'.png'))
        await channel.send(file=discord.File(path+Sub_Top_Rune+'.png'))
        await channel.send(file=discord.File(path+Sub_Bot_Rune+'.png'))
    elif re.fullmatch('^![가-힣]{1,3}\s\d티어', message.content):#op티어 적용 확인 못함
        text = message.content.replace('!', '').replace('티어', '')
        position, tier = re.split('\s',text)
        soup = getBSoup('https://www.op.gg/champion/statistics')

        if position == '탑' or position == '망나니':
            position = 'TOP'
        elif position == '정글' or position == '개백정' or position == '백정':
            position = 'JUNGLE'
        elif position == '미드' or position == '황족' or position == '근본':
            position = 'MID'
        elif position == '원딜' or position == '바텀' or position == '숟가락' or position == '젓가락':
            position = 'ADC'
        elif position == '서포터' or position == '서폿' or position == '도구' or position == '혜지':
            position = 'SUPPORT'
        else:
            await channel.send('알 수 없는 키워드입니다.')
            return None
        Tier_img = soup.select('tbody.tabItem.champion-trend-tier-' + position + '> tr > td:nth-child(7) > img')
        Tier_champ = []
        for champ in Tier_img:
            if champ.get('src') == '//opgg-static.akamaized.net/images/site/champion/icon-champtier-'+tier+'.png':
                Tier_champ.append(champ)
        embed = discord.Embed( color=discord.Colour.blue())
        name = ''
        for champ in Tier_champ:
            champ = champ.find_parent('tr')
            name = name + champ.select_one('div.champion-index-table__name').text.strip() + '\n'
        embed.add_field(name=position+' '+tier+'티어', value=name, inline=False)
        await channel.send(embed=embed)
    elif message.content.startswith('!검색'):
        search_keyword = message.content.replace('!검색 ', '').strip()
        if search_keyword:
            url = 'https://www.youtube.com/results?search_query=' + search_keyword.replace(' ', '+')
            soup = getBSoup(url)
            videos = soup.select('.yt-uix-tile-link yt-ui-ellipsis yt-ui-ellipsis-2 yt-uix-sessionlink      spf-link ')
            videos = soup.select('div > h3 > a')
            embed = discord.Embed(title='검색 결과', color=discord.Colour.red())
            if videos:
                del searched[:]
                for index in range(len(videos)):
                    if index > 6:
                        break
                    title = videos[index].get('title')
                    href = 'https://www.youtube.com'+videos[index].get('href')
                    searched.append(href)
                    await channel.send(embed=discord.Embed(title=str(index)+'  '+title, url=href, color=discord.Colour.red()))
            else:
                await channel.send('검색 결과가 존재하지 않습니다.')
        else:
            await channel.send('검색어를 입력해 주세요.')
    elif message.content.startswith('!동전'):
        embed = discord.Embed(title='동전 던지기!', color=discord.Color.red())
        rand = random.randrange(1,6001)
        if rand == 1:
            result = '동전이 섰다!'
        elif random.choice([True, False]):
            result = '前!'
        else:
            result = '後!'
        embed.add_field(name='결과', value=result, inline=False)
        await channel.send(embed=embed)
    elif message.content.startswith('!주사위'):
        embed = discord.Embed(title='주사위 굴리기!', color=discord.Color.red())
        result = random.randrange(1, 7)
        embed.add_field(name='결과', value=result, inline=False)
        await channel.send(embed=embed)
    elif message.content.startswith('!타이머'): 
        timetext = message.content.replace('!타이머', '').strip()
        time = 0
        if not timetext:
            await channel.send('시간을 입력해 주세요')
        if '시간' in timetext:
            time = 3600*(int)(timetext[0:timetext.find('시간')])
            timetext = timetext[timetext.find('시간')+2:]
        elif '시' in timetext:
            time = 3600*(int)(timetext[0:timetext.find('시')])
            timetext = timetext[timetext.find('시')+1:]
        if '분' in timetext:
            time = time + 60*(int)(timetext[0:timetext.find('분')])
            timetext = timetext[timetext.find('분')+1:]
        if '초' in timetext:
            time = time + (int)(timetext[0:timetext.find('초')])
        if time != 0 and time <= 86400:
            await channel.send(str(time) + '초 타이머 시작')
            await asyncio.sleep(float(time))
            await channel.send('타이머 끝')
    elif message.content.startswith('!리스트 추가'):
        text = message.content.replace('!리스트 추가', '').strip()
        if text:
            textList = text.split(',')
            for text in textList:
                text = text.strip()
                if text in learnList:
                    await channel.send('이미 리스트에 존재하는 word입니다.')   
                else:
                    learnList.append(text)
                    await channel.send(learnList[-1] + ' 추가되었습니다')
        else:
            await channel.send('키워드를 입력해 주세요.')
    elif message.content.startswith('!리스트 확인'):
        if not learnList:
            await channel.send('리스트가 비어 있습니다.')
        else:
            embed = discord.Embed(title = '리스트', color=discord.Color.green())
            text = ''
            for learn in learnList:
                text = text + '\n' + learn
            text = text.replace('\n', '', 1)
            embed.add_field(name='목록', value=text)
            await channel.send(embed=embed)
    elif message.content.startswith('!리스트 뽑기'):
        if not learnList:
            await channel.send('리스트가 비어 있습니다.')
        else:
            embed = discord.Embed(title = '리스트 뽑기', color=discord.Color.red())
            embed.add_field(name='결과', value=random.choice(learnList), inline=False)
            await channel.send(embed=embed)
    elif message.content.startswith('!리스트 초기화'):
        global votedDic
        del learnList[:]
        votedDic = {}
        await channel.send('리스트 초기화되었습니다.')
    elif message.content.startswith('!리스트 제거'):
        text = message.content.replace('!리스트 제거', '').strip()
        if text:
            if text in learnList:
                learnList.remove(text)
                for key, value in votedDic.items():
                    if value == text:
                        del(votedDic[key])
                        break
                await channel.send(text + ' 제거되었습니다.')
            else:
                await channel.send(text + ' 는 리스트에 존재하지 않습니다.')
        else:
            await channel.send('키워드를 입력해 주세요.')
    elif message.content.startswith('!투표 확인'):
        if not votedDic:
            await channel.send('투표된 키워드가 없습니다.')
        else:
            voteCount = {}
            for value in learnList:
                voteCount[value] = 0
            embed = discord.Embed(title = '투표', color = discord.Colour.green())
            for name, value in votedDic.items():
                embed.add_field(name=name,value=value,inline=False)
                voteCount[value] = voteCount[value] + 1
            text = ''
            for name, value in voteCount.items():
                text = text + name + ' ' + str(value) + '표\n'
            embed.add_field(name='총계', value=text, inline=False)
            await channel.send(embed=embed)
    elif message.content.startswith('!투표 초기화'):
        votedDic = {}
        await channel.send('투표가 초기화되었습니다.')
    elif message.content.startswith('!투표 취소'):
        await channel.send(votedDic[messagesendname] + '을(를) ' + messagesendname + '(이)가 취소하였습니다.')
        del(votedDic[messagesendname])
    elif message.content.startswith('!투표'):
        if not learnList:
            await channel.send('리스트가 비어 있습니다.')
        else:
            text = message.content.replace('!투표', '').strip()
            if text:
                if text in learnList:
                    votedDic[messagesendname] = text
                    await channel.send(messagesendname+' -> '+text+' 투표하였습니다.')
                else:
                    await channel.send('리스트에 ' + text + ' 가 존재하지 않습니다.')
            else:
                await channel.send('키워드를 입력해 주세요.')
    elif message.content.startswith('!재생'):
        url = message.content.replace('!재생', '').strip()
        if not url:
            await channel.send('url을 발견하지 못했습니다.')
            return None
        elif re.fullmatch('[0-6]', url):
            if searched:
                url = searched[int(url)]
        await Cog.play_(ctx, search=url)
    elif message.content.startswith('!pause'):
        await Cog.pause_(ctx)
    elif message.content.startswith('!resume'):
        await Cog.resume_(ctx)
    elif message.content.startswith('!skip'):
        await Cog.skip_(ctx)
    elif message.content.startswith('!nowplaying'):
        await Cog.now_playing_(ctx)
    elif message.content.startswith('!stop'):
        await Cog.stop_(ctx)
    elif message.content.startswith('!queueinfo'):
        await Cog.queue_info(ctx)
    elif message.content.startswith('!볼륨'):
        volume = message.content.replace('!볼륨', '').strip()
        volume = float(volume)
        await Cog.change_volume(ctx, vol=volume)
    elif message.content.startswith('!번역'):
        translate_url = "https://openapi.naver.com/v1/papago/n2mt"
        detectlang_url = "https://openapi.naver.com/v1/papago/detectLangs"
        client_id = "2gMRoiw06wiRi79S6rk_"
        client_secret = "6Xi6lacoB9"
        headers = {"X-Naver-Client-Id": client_id, "X-Naver-Client-Secret": client_secret}
        text = message.content.replace('!번역', '')
        response = requests.post(detectlang_url, headers=headers, data={"query": text})
        langCode = response.json()["langCode"]
        if langCode == 'ko':
            await channel.send('한국어번역모대')
        elif langCode == 'unk':
            await channel.send('언어를 감지하지 못했습니다.')
        else:
            params = {"source": langCode, "target": "ko", "text": text}
            response = requests.post(translate_url, headers=headers, data=params)
            result = response.json()
            translatedText = result["message"]["result"]["translatedText"]
            embed = discord.Embed(title = langCode + ' 번역 결과', description=translatedText, color=discord.Color.green())
            await channel.send(embed=embed)
    elif re.fullmatch('^!섯다 \d', message.content):
        playerCount = int(message.content.replace('!섯다 ', ''))
        await Randomsutda(playerCount, channel)
    elif message.content.startswith("!가위") or message.content.startswith("!바위") or message.content.startswith("!보"):
        await channel.send(random.choice(["가위","바위","보"]))
    elif message.content.startswith("!가바보") or message.content.startswith("가위바위보"):
        await channel.send(random.choice(["가위","바위","보"]))

def getBSoup(link):
    header = {'User-Agent': 'Mozilla/5.0', 'Accept-Language':'ko-KR'}
    html = requests.get(link, headers = header)
    soup = BeautifulSoup(html.text, 'lxml')
    return soup

def translate_champion(champ):
    champ = champ.replace(' ', '')
    champDic = {
        '가렌': 'garen',
        '갈리오': 'galio',
        '갱플랭크': 'gangplank',
        '그라가스':'gragas',
        '그레이브즈':'graves',
        '나르':'gnar',
        '나미':'nami',
        '나서스':'nasus',
        '노틸러스':'nautilus',
        '녹턴':'nocturne',
        '누누와 윌럼프':'nunu',
        '니달리':'nidalee',
        '니코':'neeko',
        '다리우스':'darius',
        '다이애나':'diana',
        '드레이븐':'draven',
        '라이즈':'ryze',
        '라칸':'rakan',
        '람머스':'rammus',
        '럭스':'lux',
        '럼블':'rumble',
        '레넥톤':'renekton',
        '레오나':'leona',
        '렉사이':'reksai',
        '렝가':'rengar',
        '루시안':'lucian',
        '룰루':'lulu',
        '르블랑':'leblanc',
        '리신':'leesin',
        '리신':'leesin',
        '리븐':'riven',
        '리산드라':'lissandra',
        '마스터이':'masteryi',
        '마이':'masteryi',
        '마오카이':'maokai',
        '말자하':'malzahar',
        '말파이트':'malphite',
        '모데카이저':'mordekaiser',
        '모르가나':'morgana',
        '문도박사':'drmundo',
        '미스포츈':'missfortune',
        '미포': 'missfortune',
        '바드':'bard',
        '바루스':'varus',
        '바이':'vi',
        '베이가':'veigar',
        '베인':'vayne',
        '벨코즈':'velkoz',
        '볼리베어':'volibear',
        '브라움':'braum',
        '브랜드':'brand',
        '블라디미르':'vladimir',
        '블리츠크랭크':'blitzcrank',
        '블츠':'blitzcrank',
        '빅토르':'viktor',
        '뽀삐':'poppy',
        '사이온':'sion',
        '사일러스':'sylas',
        '샤코':'shaco',
        '세나':'senna',
        '세주아니':'sejuani',
        '세트':'sett',
        '소나':'sona',
        '소라카':'soraka',
        '쉔':'shen',
        '쉬바나':'shyvana',
        '스웨인':'swain',
        '스카너':'skarner',
        '시비르':'sivir',
        '신짜오':'xinzhao',
        '신드라':'syndra',
        '신지드':'singed',
        '쓰레쉬':'thresh',
        '아리':'ahri',
        '아무무':'amumu',
        '아우렐리온솔':'aurelionsol',
        '아이번':'ivern',
        '아지르':'azir',
        '아칼리':'akali',
        '아트록스':'aatrox',
        '아펠리오스':'aphelios',
        '알리스타':'alistar',
        '애니':'annie',
        '애니비아':'anivia',
        '애쉬':'ashe',
        '야스오':'yasuo',
        '에코':'ekko',
        '엘리스':'elise',
        '오공':'monkeyking',
        '오른':'ornn',
        '오리아나':'orianna',
        '올라프':'olaf',
        '요릭':'yorick',
        '우디르':'udyr',
        '우르곳':'urgot',
        '워윅':'warwick',
        '유미':'yuumi',
        '이렐리아':'irelia',
        '이블린':'evelynn',
        '이즈리얼':'ezreal',
        '일라오이':'illaoi',
        '자르반4세':'jarvaniv',
        '자르반':'jarvaniv',
        '자야':'xayah',
        '자이라':'zyra',
        '자크': 'zac',
        '잔나': 'janna',
        '잭스': 'jax',
        '제드': 'zed',
        '제라스': 'xerath',
        '제이스': 'jayce',
        '조이': 'zoe',
        '직스': 'ziggs',
        '진': 'jhin',
        '질리언': 'zilean',
        '징크스': 'jinx',
        '초가스': 'chogath',
        '카르마': 'karma',
        '카밀': 'camille',
        '카사딘': 'kassadin',
        '카서스': 'karthus',
        '카시오페아': 'cassiopeia',
        '카이사': 'kaisa',
        '카직스': 'khazix',
        '카타리나': 'katarina',
        '칼리스타': 'kalista',
        '케넨': 'kennen',
        '케이틀린': 'caitlyn',
        '케인': 'kayn',
        '케일': 'kayle',
        '코그모': 'kogmaw',
        '코르키': 'corki',
        '퀸': 'quinn',
        '클레드': 'kled',
        '키아나': 'qiyana',
        '킨드레드': 'kindred',
        '타릭': 'taric',
        '탈론': 'talon',
        '탈리야': 'taliyah',
        '탐켄치': 'tahmkench',
        '트런들': 'trundle',
        '트리스타나': 'tristana',
        '트타': 'tristana',
        '트린다미어': 'tryndamere',
        '트위스티드페이트': 'twistedfate',
        '트페': 'twistedfate',
        '트위치': 'twitch',
        '티모': 'teemo',
        '파이크': 'pyke',
        '판테온': 'pantheon',
        '피들스틱': 'fiddlesticks',
        '피오라': 'fiora',
        '피즈': 'fizz',
        '하이머딩거': 'heimerdinger',
        '하딩': 'heimerdinger',
        '헤카림': 'hecarim',
    }
    if champ in champDic.keys():
        return champDic[champ]
    return None

def getActiveRune(Runes):
    for Rune in Runes:
        if not 'grayscale' in Rune.get('src'):
            return Rune.get('alt')
    return None

async def Summonerinfo(sname, channel):
    url = 'https://www.op.gg/summoner/userName=' + sname.replace(' ','-').strip()
    soup = getBSoup(url)
    if soup.select_one('div.SummonerNotFoundLayout'):
        await channel.send('존재하지 않는 소환사입니다.')
    elif soup.select_one('div.TierRankInfo > div.TierRank.unranked'):
        await channel.send('언랭은 정보를 제공하지 않습니다.')
    else:
        name = soup.select_one('div.Profile > div.Information > span').text
        tierRank = soup.select_one('div.TierRankInfo > div.TierRank').text
        tierLP = soup.select_one('div.TierInfo > span.LeaguePoints').text
        win = soup.select_one('div.TierInfo > span.WinLose > span.wins').text
        lose = soup.select_one('div.TierInfo > span.WinLose > span.losses').text
        winratio = soup.select_one('div.TierInfo > span.WinLose > span.winratio').text.replace('Win Ratio ', '')
        most_champ = soup.select_one('div.MostChampionContent.tabItem.overview-stats--all > div > div:nth-child(1) > div.ChampionInfo > div.ChampionName > a').text
        embed = discord.Embed(title = name, color = discord.Colour.blue())
        embed.add_field(name='티어', value=tierRank + ' ' + tierLP, inline=False)
        embed.add_field(name='승률', value=win+' '+lose+'\n'+winratio, inline=False)
        embed.add_field(name='모스트', value=most_champ+'\n'+url, inline=False)
        await channel.send(embed=embed)

async def Randomsutda(count, channel):
    overlap = []
    for index in range(count):
        while True:
            rand = random.randrange(1,21)
            if rand in overlap:
                rand = random.randrange(1,21)
            else:
                file = 'hwatu/'+str(rand)+'.png'
                overlap.append(rand)
                await channel.send(file=discord.File(file))
                break


bot.run("토큰")