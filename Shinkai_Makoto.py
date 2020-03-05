import asyncio
import discord
import youtube_dl
import re
import requests
import random
from bs4 import BeautifulSoup
from discord.ext import commands
#LCK 스케줄, 현재 경기정보, 오늘 경기정보
bot = commands.Bot(command_prefix='!', description='신카이 마코토')
learnList = []
votedDic = {}
channel = None
#시간아닌거 제외처리 분, 시도 되게, 타이머 몇시 남았는지 확인, 스톱워치.
@bot.event
async def on_ready():
    print(bot.user.name)
    print(bot.user.id)
    print("===========")
    await bot.change_presence(status=discord.Status.online, activity=discord.Game('잉앵옹'))
    while True:
        await asyncio.sleep(float(random.randrange(360,2000)))
        await channel.send('ㅗ')

@bot.event
async def on_message(message):
    if message.author.bot:
        return None
    id = message.author.name #Embed타이틀 없애도 됨 없애도 되는거 없애기
    channel = message.channel
    if message.content.startswith('!재생'): 
        voice_channel = message.author.voice.channel
        voice_client = await voice_channel.connect()
    elif message.content.startswith('!명령어') or message.content.startswith('!help'):#말투 맞추기.
        embed = discord.Embed(title = '명령어들이에옹', color = discord.Colour.blue())
        embed.add_field(name = '!검색', value='\" !검색 <keyword> \" 형식으로 적으면 유튜브에 검색함.', inline=False)
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
        embed.add_field(name = '포지션 #티어', value='\" !<position> <tier>티어', inline=False)
        await channel.send(embed=embed)
    elif re.fullmatch('^![가-힣]{1,3}\s\d티어', message.content):
        text = message.content.replace('!', '').replace('티어', '')
        position, tier = re.split('\s',text)
        soup = getBSoup('https://www.op.gg/champion/statistics')

        if position == '탑':
            position = 'TOP'
        elif position == '정글' or position == '개백정' or position == '백정':
            position = 'JUNGLE'
        elif position == '미드' or position == '황족':
            position = 'MID'
        elif position == '원딜' or position == '바텀' or position == '숟가락' or position == '젓가락':
            position = 'ADC'
        elif position == '서포터' or position == '서폿' or position == '도구':
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
    elif message.content.startswith('!롤'):
        search_name = message.content.replace('!롤', '').strip()
        if search_name:
            url = 'https://www.op.gg/summoner/userName=' + search_name.replace(' ','-')
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
                await channel.send(embed=embed)  # RIOTAPI 를 통해 인게임 정보를 얻어보자.
        else:
            await channel.send('소환사명을 입력해 주세요.')
    elif message.content.startswith('!검색'):
        search_keyword = message.content.replace('!검색 ', '').strip()
        if search_keyword:
            url = 'https://www.youtube.com/results?search_query=' + search_keyword #채널도뽑기
            print(url)
            soup = getBSoup(url)
            #videos = soup.find_all('a', {'id':'video-title'})
            videos = soup.select('.yt-uix-tile-link yt-ui-ellipsis yt-ui-ellipsis-2 yt-uix-sessionlink      spf-link ')
            videos = soup.select('div > h3 > a')
            embed = discord.Embed(title='검색 결과', color=discord.Colour.red())
            if videos:
                for index in range(len(videos)):
                    if index > 6:
                        break
                    title = videos[index].get('title')
                    href = videos[index].get('href')
                    embed.add_field(name=title, value='https://www.youtube.com'+href, inline=False)
                await channel.send(embed=embed)
            else:
                await channel.send('검색 결과가 존재하지 않습니다.')
        else:
            await channel.send('검색어를 입력해 주세요.')
    elif message.content.startswith('!동전'):
        embed = discord.Embed(title='동전 던지기!', color=discord.Color.red())
        if random.choice([True, False]):
            result = '上!'
        else:
            result = '下!'
        embed.add_field(name='결과', value=result, inline=False)
        await channel.send(embed=embed)
    elif message.content.startswith('!주사위'):
        embed = discord.Embed(title='주사위 굴리기!', color=discord.Color.red())
        result = random.randrange(1, 7)
        embed.add_field(name='결과', value=result, inline=False)
        await channel.send(embed=embed)
    elif message.content.startswith('!타이머'):#
        sec = message.content.replace('!타이머', '').strip()
        if sec:
            await channel.send(sec + '초 타이머 시작')
            await asyncio.sleep(float(int(sec)))
            await channel.send('타이머 끝')
        else:
            await channel.send('시간를 입력해 주세요.')
    elif message.content.startswith('!리스트 추가'):#최적화
        text = message.content.replace('!리스트 추가', '').strip()
        if text:
            if ',' in text:
                textList = text.split(',')
                for text in textList:
                    textList[textList.index(text)] = text.strip()
                for txt in textList:
                    print(txt)
                    print('1')
                    if txt in learnList:
                        await channel.send('이미 리스트에 존재하는 word입니다.')   
                    else:
                        learnList.append(txt)
                        await channel.send(learnList[-1] + ' 추가되었습니다')
            else:
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
        del learnList[:]
        votedDic = {}
        await channel.send('리스트 초기화되었습니다.')
    elif message.content.startswith('!리스트 제거'):
        text = message.content.replace('!리스트 제거', '').strip()
        if text:
            if text in learnList:
                learnList.remove(text)
                await channel.send(text + ' 제거되었습니다.')
            else:
                await channel.send(text + ' 는 리스트에 존재하지 않습니다.')
        else:
            await channel.send('키워드를 입력해 주세요.')
    elif message.content.startswith('!투표 확인'):
        global votedDic
        if not votedDic:
            await channel.send('투표된 키워드가 없습니다.')
        else:
            print(votedDic)
            voteCount = {}
            #for value in votedDic.values():
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
    elif message.content.startswith('!투표'):
        if not learnList:
            await channel.send('리스트가 비어 있습니다.')
        else:
            text = message.content.replace('!투표', '').strip()
            if text:
                if text in learnList:
                    votedDic[id] = text
                    await channel.send(id+' -> '+text+' 투표하였습니다.')
                else:
                    await channel.send('리스트에 ' + text + ' 가 존재하지 않습니다.')
            else:
                await channel.send('키워드를 입력해 주세요.')
    elif re.fullmatch('^![가-힣]{1,5}.*병신', message.content):
        if '홍준혁' in message.content or '마코토' in message.content:
            await channel.send('ㄴㅇㅈ')
        else:
            if random.random() > 0.9:
                await channel.send('ㄴㅇㅈ')
            else:
                await channel.send('ㅇㅈ')
    elif re.fullmatch('^![가-힣]{1,5}느낌', message.content):
        if random.choice([True, False]):
            await channel.send('없음')
        else:
            await channel.send('있음')
    elif not message.content.find("너의 이름은"):
        await channel.send('정말 갓애니 입니다.')

def getBSoup(link):
    header = {'User-Agent': 'Mozilla/5.0', 'Accept-Language':'ko-KR'}
    html = requests.get(link, headers = header)
    soup = BeautifulSoup(html.text, 'html.parser')
    return soup

bot.run("Njg0MzExMjcwNzMxNTQ2NzAy.Xl4SYg.dSRKG6ZYluij5jqS3mzRF_hqj9U")