import asyncclick as click
import dataclasses
import contextvars
import json
import logging
import pytest
import trio
from contextlib import asynccontextmanager, suppress
from functools import partial
from trio_websocket import (
    serve_websocket,
    connect_websocket,
    ConnectionClosed
)

from models import Bus, WindowBounds

DELAY_UPDATE = 0.3

buses = dict()
sockets_with_bounds = contextvars.ContextVar('sockets_with_bounds', default={})
test_mode_var = contextvars.ContextVar('test_mode', default=False)
bus = contextvars.ContextVar('bus')

logger = logging.getLogger('serv_bus')


class JsonFieldsError(Exception):

    def __init__(self, error_message):
        self.error_message = error_message

    def __str__(self):
        return ','.join(self.error_message)


@asynccontextmanager
async def handle_json_errors(ws):
    test_mode = test_mode_var.get()
    try:
        yield
    except json.JSONDecodeError:
        error = {"errors": ["Requires valid JSON"], "msgType": "Errors"}
        logger.info(f'Response: {error}')
        if test_mode:
            raise
        await ws.send_message(json.dumps(error))
    except JsonFieldsError as fields:
        error = {"errors": [f"Requires {str(fields)} specified"], "msgType": "Errors"}
        logger.info(f'Response: {error}')
        if test_mode:
            raise
        await ws.send_message(json.dumps(error))


def set_log_level(context, verbose):
    logger.setLevel(verbose * 10)


async def listen_buses(ws):
    async with handle_json_errors(ws):
        bus_info = json.loads(await ws.get_message())
        fields_not_matched = set(bus_info.keys()) ^ {field.name for field in dataclasses.fields(Bus)}
        if fields_not_matched:
            raise JsonFieldsError(fields_not_matched)
        buses[bus_info['busId']] = Bus(*bus_info.values())


async def listen_browser(ws):
    async with handle_json_errors(ws):
        while True:
            bounds = sockets_with_bounds.get()[ws]
            browser_message = json.loads(await ws.get_message())
            fields_not_matched = set(browser_message.keys()) ^ {'data', 'msgType'}
            if fields_not_matched:
                raise JsonFieldsError(fields_not_matched)
            bounds.update(*browser_message['data'].values())
            logger.info(browser_message)
            await trio.sleep(DELAY_UPDATE)


async def read_buses(request):
    ws = await request.accept()
    while True:
        await listen_buses(ws)


async def send_buses(ws):
    while True:
        bounds = sockets_with_bounds.get()[ws]
        buses_inside = [
            dataclasses.asdict(bus) for bus in buses.values() if bounds.is_inside(bus.lat, bus.lng)
        ]
        await ws.send_message(
            json.dumps({
                "msgType": "Buses",
                "buses": buses_inside
            })
        )
        await trio.sleep(DELAY_UPDATE)


def update_active_sockets(ws):
    active_sockets_with_bounds = {
        key: values for key, values in sockets_with_bounds.get().items() if not key.closed
    }
    if not active_sockets_with_bounds.get(ws):
        active_sockets_with_bounds[ws] = WindowBounds(0, 0, 0, 0)
    return active_sockets_with_bounds


async def talk_to_browser(request):
    ws = await request.accept()
    sockets_with_bounds.set(update_active_sockets(ws))
    async with trio.open_nursery() as nursery:
        nursery.start_soon(listen_browser, ws)
        nursery.start_soon(send_buses, ws)


@click.command()
@click.option('-p', '--bus_port', default=8080, help='Порт для имитатора автобусов.')
@click.option('-r', '--browser_port', default=8000, help='Порт для браузера.')
@click.option('-v', '--verbose', count=True, callback=set_log_level, help='Настройка логирования.')
async def main(**kwargs):
    logging.basicConfig()
    while True:
        try:
            async with trio.open_nursery() as nursery:
                nursery.start_soon(
                    partial(serve_websocket, read_buses, '127.0.0.1', kwargs['bus_port'], ssl_context=None)
                )
                nursery.start_soon(
                    partial(serve_websocket, talk_to_browser, '127.0.0.1', kwargs['browser_port'], ssl_context=None)
                )
        except ConnectionClosed:
            continue


if __name__ == '__main__':
    with suppress(KeyboardInterrupt):
        main(_anyio_backend="trio")


async def test_bus_data_decode(nursery):
    test_mode_var.set(True)

    async def handler(request):
        ws = await request.accept()
        await ws.send_message(
            '{"busId": "\t", "lat": 0, "lng": 0, "route": "\t"}'
        )

    server = await nursery.start(
        partial(serve_websocket, handler, '127.0.0.1', 0, ssl_context=None)
    )

    connection = await connect_websocket(
        nursery, '127.0.0.1', server.port, '/', use_ssl=False
    )

    sockets_with_bounds.set(update_active_sockets(connection))

    with pytest.raises(json.JSONDecodeError):
        await listen_buses(connection)


async def test_bus_data_fields(nursery):
    test_mode_var.set(True)

    async def handler(request):
        ws = await request.accept()
        await ws.send_message('{"bus": ""}')

    server = await nursery.start(
        partial(serve_websocket, handler, '127.0.0.1', 0, ssl_context=None)
    )

    connection = await connect_websocket(
        nursery, '127.0.0.1', server.port, '/', use_ssl=False
    )

    sockets_with_bounds.set(update_active_sockets(connection))

    with pytest.raises(JsonFieldsError):
        await listen_buses(connection)


async def test_browser_data_decode(nursery):
    test_mode_var.set(True)

    async def handler(request):
        ws = await request.accept()
        await ws.send_message(
            '{"data": "\t", "msgType": "\t"}'
        )

    server = await nursery.start(
        partial(serve_websocket, handler, '127.0.0.1', 0, ssl_context=None)
    )

    connection = await connect_websocket(
        nursery, '127.0.0.1', server.port, '/', use_ssl=False
    )

    sockets_with_bounds.set(update_active_sockets(connection))

    with pytest.raises(json.JSONDecodeError):
        await listen_browser(connection)


async def test_browser_data_fields(nursery):
    test_mode_var.set(True)

    async def handler(request):
        ws = await request.accept()
        await ws.send_message('{"id": "", "msg": ""}')

    server = await nursery.start(
        partial(serve_websocket, handler, '127.0.0.1', 0, ssl_context=None)
    )

    connection = await connect_websocket(
        nursery, '127.0.0.1', server.port, '/', use_ssl=False
    )

    sockets_with_bounds.set(update_active_sockets(connection))

    with pytest.raises(JsonFieldsError):
        await listen_browser(connection)
