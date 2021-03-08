import asyncclick as click
import os
import json
import logging
import random
import trio
from contextlib import suppress
from more_itertools import first, last
from sys import stderr
from trio_websocket import open_websocket_url, ConnectionClosed, HandshakeError

logger = logging.getLogger('fake_bus')


def set_log_level(context, verbose):
    logger.setLevel(verbose * 10)


def generate_bus_id(route_id, bus_index, emulator_id=''):
    return f'{emulator_id}{route_id}-{bus_index}'


def relaunch_on_disconnect(async_function):
    async def inner(server_address, receive_channel):
        while True:
            try:
                await async_function(server_address, receive_channel)
            except (ConnectionClosed, HandshakeError) as error:
                logger.info('Connection attempt failed: %s' % error)
                await trio.sleep(5)
                continue
    return inner


def load_routes(routes_number, directory_path='routes'):
    for id, filename in enumerate(os.listdir(directory_path)):
        if not filename.endswith(".json"):
            continue
        if routes_number and id > routes_number:
            break
        filepath = os.path.join(directory_path, filename)
        with open(filepath, 'r') as file_handler:
            yield json.loads(file_handler.read())


async def run_bus(send_channel, bus_id, route, start_offset=0, refresh_timeout=0):
    while True:
        for bus_point in route['coordinates'][start_offset:]:
            await send_channel.send(
                json.dumps(
                    {
                        'busId': bus_id,
                        'lat': first(bus_point, 0),
                        'lng': last(bus_point, 0),
                        'route': route['name']
                    },
                    ensure_ascii=False
                )
            )
            await trio.sleep(refresh_timeout)
        start_offset = 0


@relaunch_on_disconnect
async def send_updates(server_address, receive_channel):
    async with open_websocket_url(server_address, ssl_context=None) as conn:
        async for value in receive_channel:
            await conn.send_message(value)


def get_channels(websockets_number):
    channels = []
    for _ in range(websockets_number):
        channels.append(trio.open_memory_channel(0))
    return channels


@click.command()
@click.option(
    '-s', '--server', default='ws://127.0.0.1:8080/ws', help='Адрес сервера.'
)
@click.option('-r', '--routes_number', default=0, help='Количество маршрутов.')
@click.option(
    '-b', '--buses_per_route', default=3,
    help='Количество автобусов на каждом маршруте.'
)
@click.option(
    '-w', '--websockets_number', default=10,
    help='Количество открытых веб-сокетов.'
)
@click.option(
    '-e', '--emulator_id', default='',
    help='Префикс к busId на случай запуска нескольких экземпляров имитатора.'
)
@click.option(
    '-t', '--refresh_timeout', default=3,
    help='Задержка в обновлении координат сервера.'
)
@click.option(
    '-v', '--verbose', count=True, callback=set_log_level, help='Настройка логирования.'
)
async def main(**kwargs):
    logging.basicConfig()
    try:
        async with trio.open_nursery() as nursery:
            channels = get_channels(kwargs['websockets_number'])
            for route in load_routes(kwargs['routes_number']):
                send_channel, receive_channel = random.choice(channels)
                for bus_on_route in range(kwargs['buses_per_route']):
                    start_offset = random.randrange(len(route['coordinates']))
                    bus_id = generate_bus_id(
                        route['name'], str(bus_on_route), kwargs['emulator_id']
                    )
                    nursery.start_soon(
                        run_bus, send_channel, bus_id,
                        route, start_offset, kwargs['refresh_timeout']
                    )
                nursery.start_soon(send_updates, kwargs['server'], receive_channel)
    except OSError as error:
        logger.info('Connection attempt failed: %s' % error, file=stderr)


if __name__ == '__main__':
    with suppress(KeyboardInterrupt):
        main(_anyio_backend="trio")
