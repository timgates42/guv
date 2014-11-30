import sys
import logging
import errno
import greenlet
from abc import ABCMeta, abstractmethod
import socket

from . import greenpool, patcher

from .greenio import GreenSocket
from .hubs import get_hub

original_socket = patcher.original('socket')

log = logging.getLogger('eventlet')

NONBLOCKING = {errno.EAGAIN, errno.EWOULDBLOCK}


def serve(sock, handle, concurrency=1000):
    pool = greenpool.GreenPool(concurrency)
    server = ServerLoop(sock, handle, pool, 'spawn_n')
    server.start()


def listen(addr, family=socket.AF_INET, backlog=511):
    server_sock = socket.socket(family, socket.SOCK_STREAM)

    if sys.platform[:3] != 'win':
        server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

    server_sock.bind(addr)
    server_sock.listen(backlog)

    return server_sock


class StopServe(Exception):
    """Exception class used for quitting :func:`~eventlet.serve` gracefully
    """
    pass


class AbstractServer(metaclass=ABCMeta):
    def __init__(self, server_sock, client_handler_cb, pool=None, spawn=None):
        """
        If pool and spawn are None (default), bare greenlets will be used and the spawn mechanism
        will be greenlet.switch(). This is the simplest and most direct way to spawn greenlets to
        handle client requests, however it is not the most stable.

        If more control is desired over client handlers, specify a greenlet pool class such as
        `GreenPool`, and specify a spawn mechanism. If specifying a pool class, the the name of the
        spawn method must be passed as well.

        The signature of client_handler_cb is as follows::

            Callable(sock: socket, addr: tuple[str, int]) -> None

        :param pool: greenlet pool class or None
        :param spawn: 'spawn', or 'spawn_n', or None
        :type spawn: str or None
        """
        self.server_sock = server_sock
        self.client_handler_cb = client_handler_cb

        if pool is None:
            # use bare greenlets
            self.pool = None
            log.debug('server configured: use bare greenlets')
        else:
            # create a pool instance
            self.pool = pool
            self.spawn = getattr(self.pool, spawn)
            log.debug('server configured: use {}.{}'.format(pool, spawn))

        self.hub = get_hub()
        self.loop = self.hub.loop

        self.address = server_sock.getsockname()[:2]

    @abstractmethod
    def start(self):
        """Start the server
        """
        pass

    @abstractmethod
    def stop(self):
        """Stop the server
        """

    def handle_error(self, msg, level=logging.ERROR, exc_info=True):
        log.log(level, '{0}: {1} --> closing'.format(self, msg), exc_info=exc_info)
        self.stop()

    def _spawn(self, client_sock, addr):
        """Spawn a client handler using the appropriate spawn mechanism

        :param client_sock: client socket
        :type client_sock: socket.socket or GreenSocket
        :param addr: address tuple
        :type addr: tuple[str, int]
        """
        if self.pool is None:
            g = greenlet.greenlet(self.client_handler_cb)
            g.switch(client_sock, addr)
        else:
            self.spawn(self.client_handler_cb, client_sock, addr)


class ServerLoop(AbstractServer):
    """Standard server implementation not directly dependent on pyuv

    This requires a GreenSocket
    """

    def start(self):
        log.debug('{0} started on {0.address}'.format(self))
        while True:
            try:
                client_sock, addr = self.server_sock.accept()
                self._spawn(client_sock, addr)
            except StopServe:
                log.debug('{0} stopped'.format(self))
                return

    def stop(self):
        logging.debug('{0}: stopping'.format(self))
