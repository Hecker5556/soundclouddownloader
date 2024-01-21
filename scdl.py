import asyncio, aiohttp, aiofiles, re, json, os, shutil, random, logging, argparse
from env import clientid
from typing import Literal
from tqdm.asyncio import tqdm

class scdl:
    async def extracturls(session: aiohttp.ClientSession, link):
        async with session.get(link) as r:
            rtext = await r.text()
        pattern = r'(\[\{\"hydratable\":\"anonymousId\",\"data\":(?:.*?));</script>'
        match = re.findall(pattern, rtext)[0]
        main = json.loads(match)
        data = {}
        isplaylist = False
        for i in main:
            if isinstance(i, dict):
                if i.get('data'):
                    if isinstance(i.get('data'), dict):
                        if i.get('data').get('tracks'):
                            isplaylist = True
                            for index, j in enumerate(i['data']['tracks']):
                                data[index] = j.get('id')
                            data['title'] = i.get('data').get('title')
                            logging.debug(data['title'])
                            break
                        elif i.get('data').get('media'):
                            for index, j in enumerate(i['data']['media']['transcodings']):
                                data[index] = j
                            
                            data['title'] = i.get('data').get('title')
                            data['user'] = i.get('data').get('user')
                            break
        return data, isplaylist
    class novalidformat(Exception):
        def __init__(self, *args: object) -> None:
            super().__init__(*args)
    async def download(link: str, protocol: Literal['hls', 'progressive'] = 'progressive', 
                       format_audio: Literal['mpeg', 'opus'] = 'mpeg', verbose=False):
        """
        link (str): link to a song
        protocol ('hls' or 'progressive') (progressive by default): whether to download segmented version or direct
        format_audio ('mpeg' or 'opus') (mpeg by default): whether to download mpeg encoded (mp3) or opus encoded (ogg) audio
        """
        if verbose:
            logging.basicConfig(level=logging.DEBUG, format='%(message)s')
        else:
            logging.basicConfig(level=logging.INFO, format='%(message)s')
        async with aiohttp.ClientSession() as session:
            data, isplaylist = await scdl.extracturls(session, link)
            alldata = []
            if isplaylist:
                ids = [str(x) for x in data.values() if x != 'title']
                chunks, remainder = divmod(len(ids), 10)
                start = 0
                logging.info(f'grabbing info for {len(ids)} songs...')
                for _ in range(chunks):

                    params = {
                        'ids': ','.join(ids[start:start+10]),
                        'client_id': clientid
                    }
                    async with session.get('https://api-v2.soundcloud.com/tracks', params=params) as r:
                        alldata.append(await r.json())
                    start += 10
                if remainder>0:
                    params = {
                        'ids': ','.join(ids[start:]),
                        'client_id': clientid
                    }
                    async with session.get('https://api-v2.soundcloud.com/tracks', params=params) as r:
                        alldata.append(await r.json())
            url = None
            params = {
                'client_id': clientid
            }
            if isplaylist:
                logging.info('downloading playlist...')
                allinfo = {}
                foldername = "".join([x for x in data['title'] if x not in '"\\/:*?<>|()'])
                if not os.path.exists(foldername):
                    os.mkdir(foldername)
                progress = tqdm(total=len(ids), colour='red')
                for chunk in alldata:
                    for medias in chunk:
                        exists = False
                        for i in os.listdir(foldername):
                            if "".join([x for x in medias['title'] if x not in '"\\/:*?<>|()']) in i:
                                logging.debug(f'{medias["title"]} already in playlist! skipping...')
                                progress.update(1)
                                exists = True
                                break
                        if exists:
                            continue
                        media = medias['media']['transcodings']
                        for value in media:
                            if value['format'].get('protocol') == protocol and format_audio in value['format'].get('mime_type'):
                                url = value.get('url')
                                prot = value.get('format').get('protocol')
                                async with session.get(url, params=params) as r:
                                    url = await r.json()
                                    url = url.get('url')
                                break
                        if not url:
                            logging.info(f'couldnt get right format for {medias["title"]}')
                            url = media[0].get('url')
                            prot = media[0].get('format').get('protocol')
                            async with session.get(url, params=params) as r:
                                url = await r.json()
                                url = url.get('url')
                        filename, data2 = await scdl.downloader(prot, session, url, format_audio, medias, verbose)

                        try:
                            shutil.move(filename, foldername)
                        except shutil.Error:
                            logging.debug(f'\noverwritten {filename}...\n')
                            os.remove(os.path.join(foldername, filename))
                            shutil.move(filename, foldername)
                        allinfo[os.path.join(foldername, filename)] = data2
                        progress.update(1)
                progress.close()
                ## just incase all already exist
                allinfo["filelist"] = [os.path.join(foldername, filename) for filename in os.listdir(foldername)]
                return allinfo


            else:
                for key, value in data.items():
                    if isinstance(value, dict):
                        if value.get('format'):
                            if value['format'].get('protocol') == protocol and format_audio in value['format'].get('mime_type'):
                                url = value.get('url')
                                async with session.get(url, params=params) as r:
                                    url = await r.json()
                                    url = url.get('url')
                                break
                if not url:
                    raise scdl.novalidformat(f"no valid format found for settings: {protocol}, {format_audio}")
                filename, data = await scdl.downloader(protocol, session, url, format_audio, data, verbose)
                return filename, data
            

            
    async def downloader(protocol, session: aiohttp.ClientSession, url, format_audio, data, verbose: bool):
        tasks = []
        links = []
        filenames = []
        threads = asyncio.Semaphore(10)
        colours = ['red', 'green', 'yellow', 'blue', 'magenta', 'cyan', 'white', 'black']
        if protocol == 'hls':
            progress = tqdm(total=None, unit='iB', unit_scale=True, colour=random.choice(colours), disable=(not verbose))
            async with session.get(url) as r:
                manifestdata = await r.text()
            for i in manifestdata.split('\n'):
                if i.startswith('https'):
                    links.append(i)
            for index, link in enumerate(links):
                filename = f'segmenta{index}'+ ('.mp3' if format_audio == 'mpeg' else '.ogg')
                filenames.append(filename)
                tasks.append(scdl.downloadworker(link, filename, session, threads, progress))
            await asyncio.gather(*tasks)
            filename = data.get('title') + ('.mp3' if format_audio == 'mpeg' else '.ogg')
            filename = "".join([x for x in filename if x not in '"\\/:*?<>|()'])
            filenames = sorted(filenames, key = lambda x: int(x.split('a')[1].split('.')[0]))
            async with aiofiles.open(filename, 'wb') as f1:
                for file in filenames:
                    async with aiofiles.open(file, 'rb') as f2:
                        await f1.write(await f2.read())
                    os.remove(file)
            progress.close()
        else:
            async with session.get(url) as r:
                progress = tqdm(total=int(r.headers.get('content-length')), unit='iB', unit_scale=True, colour=random.choice(colours),  disable=(not verbose))
                filename = data.get('title') + ('.mp3' if format_audio == 'mpeg' else '.ogg')
                filename = "".join([x for x in filename if x not in '"\\/:*?<>|()'])
                async with aiofiles.open(filename, 'wb') as f1:
                    while True:
                        chunk = await r.content.read(1024)
                        if not chunk:
                            break
                        await f1.write(chunk)
                        progress.update(len(chunk))
                progress.close()
        return filename, data
        
    async def downloadworker(link: str, filename: str, session: aiohttp.ClientSession, 
                             threads: asyncio.Semaphore, progress: tqdm):
        async with threads:
            async with session.get(link) as r:
                async with aiofiles.open(filename, 'wb') as f1:
                    while True:
                        chunk = await r.content.read(1024)
                        if not chunk:
                            break
                        await f1.write(chunk)
                        progress.update(len(chunk))
            
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='download soundcloud songs and playlists')
    parser.add_argument("link", help='link to the song/playlist')
    parser.add_argument("--protocol", "-p", choices=['hls', 'progressive'], default='progressive', help='which protocol to use to download (hls is fragmented, progressive is direct link)')
    parser.add_argument("--format-audio", "-f", choices=['mpeg', 'opus'], default = 'mpeg', help='which format to download, mpeg being mp3, opus being ogg')
    parser.add_argument("--verbose", "-v", action="store_true", help="whether to directly show downloads happening and whatnot (if off only shows progress of downloading every song in playlist)")
    args = parser.parse_args()
    asyncio.run(scdl.download(args.link, args.protocol, args.format_audio, args.verbose))
