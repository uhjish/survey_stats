import random
import uvloop
import asyncio
import ujson as json
from aiohttp import ClientSession
from survey_stats import log

logger = log.getLogger()

asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())

MAX_CONCURRENT_REQ = 100
headers = {'content-type': 'application/json'}

def key_from_data(*args, **kwargs):
    logger.info(args)
    logger.info(kwargs)
    if 'data' in kwargs:
        return keys.hashkey(kwargs['data'])
    elif len(args)>2:
        return keys.hashkey(args[1])
    return keys.hashkey(*args, **kwargs)

async def fetch(url, data, session):
    async with session.post(url, data=data, headers=headers) as response:
        delay = response.headers.get('DELAY')
        date = response.headers.get('DATE')
        print('{}:{}, data={}, delay={}'.format(date, response.url, data, delay))
        return await response.json()


async def fetch_all(slices, worker_url):
    rqurl = worker_url + '/stats'
    tasks = []

    # Create client session that will ensure we dont open new connection
    # per each request.
    async with ClientSession() as session:
        for s in slices:
            print(json.dumps(s))
            # pass Semaphore and session to every GET request
            task = asyncio.ensure_future(fetch(rqurl, json.dumps(s), session))
            tasks.append(task)

        responses = await asyncio.gather(*tasks)
        return [r for r in responses]
