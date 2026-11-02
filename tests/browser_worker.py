"""One temporary Chromium process with isolated, short-lived client journeys."""

from contextlib import contextmanager
from threading import Event, Thread

from poolhouse.scrape.browser import Window, browser


class DebugWindow(Window):
    def args(self):
        return [*super().args(), '--remote-debugging-address=127.0.0.1', '--remote-debugging-port=0']


class BrowserWorker:
    def __init__(self, profile):
        self.profile = profile
        self.ready, self.stop = Event(), Event()
        self.failure, self.endpoint = None, ''
        self.client, self.driver, self.manager = None, None, None
        self.depth = 0
        self.thread = Thread(target=self._serve, name='test-browser-owner')

    def _serve(self):
        from playwright.sync_api import Error

        try:
            with browser(DebugWindow(profile=self.profile, headless=True, channel='chromium-headless-shell')):
                port, path = (self.profile / 'DevToolsActivePort').read_text().splitlines()
                if not port.isdecimal() or not path.startswith('/devtools/browser/'):
                    raise RuntimeError('the browser did not publish a valid local debugging endpoint')
                self.endpoint = f'ws://127.0.0.1:{int(port)}{path}'
                self.ready.set()
                self.stop.wait()
        except (Error, OSError, ValueError, RuntimeError) as error:
            self.failure = error
        finally:
            self.ready.set()

    def start(self):
        self.thread.start()
        if not self.ready.wait(30):
            self.close()
            raise RuntimeError('the test browser owner did not become ready')
        if self.failure is not None:
            self.close()
            raise RuntimeError('the test browser owner failed') from self.failure

    def close(self):
        self.stop.set()
        self.thread.join(30)
        if self.thread.is_alive():
            raise RuntimeError('the test browser owner did not stop')
        if self.failure is not None:
            raise RuntimeError('the test browser owner failed') from self.failure

    @contextmanager
    def connection(self, play=None):
        if self.depth and play is not None and play is not self.driver:
            raise RuntimeError('nested browser journeys must use the same driver')
        if not self.depth:
            from playwright.sync_api import sync_playwright

            self.manager = sync_playwright().start() if play is None else None
            self.driver = self.manager or play
            try:
                self.client = self.driver.chromium.connect_over_cdp(self.endpoint)
            except BaseException:
                if self.manager is not None:
                    self.manager.stop()
                self.client = self.driver = self.manager = None
                raise
        self.depth += 1
        try:
            yield self.client
        finally:
            self.depth -= 1
            if not self.depth:
                try:
                    self.client.close()
                finally:
                    try:
                        if self.manager is not None:
                            self.manager.stop()
                    finally:
                        self.client = self.driver = self.manager = None

    @contextmanager
    def pages(self, *, play=None, viewport=None):
        with self.connection(play) as client:
            context = client.new_context(
                viewport=viewport or {'width': 1440, 'height': 1000}, accept_downloads=True)
            try:
                context.set_default_timeout(60_000)
                yield context.new_page()
            finally:
                context.close()
