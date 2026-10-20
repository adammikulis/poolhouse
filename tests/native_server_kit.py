"""Scripted native CPU HTTP fixtures prove protocol wiring, never model ability."""
import json
import shutil
import subprocess
import sys
from contextlib import contextmanager

import pytest


def _body(content=None, calls=None):
    return json.dumps({'model': 'fixture', 'choices': [{'finish_reason': 'tool_calls' if calls else 'stop',
        'message': {'content': content, **({'tool_calls': calls} if calls else {})}}]}, separators=(',', ':'))


HEALTH = json.dumps({'data': [], 'status': 'ok'}, separators=(',', ':'))
CHAT = _body('ready')
CALL = _body(calls=[{'id': 'call_setup', 'type': 'function', 'function': {
    'name': 'poolhouse_setup_sum', 'arguments': '{"left":2,"right":3}'}}])
RESULT = _body('5')

SOURCE = r'''#include <arpa/inet.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <unistd.h>
int main(int argc, char **argv) {
    int port=0;
    for(int i=1;i<argc;i++) if(!strcmp(argv[i],"--help") || !strcmp(argv[i],"--version")) {
        puts("usage: fixture"); return 0;
    }
    for(int i=1;i+1<argc;i++) if(!strcmp(argv[i],"--port")) port=atoi(argv[i+1]);
    signal(SIGPIPE,SIG_IGN);
    int s=socket(AF_INET,SOCK_STREAM,0), yes=1;
    setsockopt(s,SOL_SOCKET,SO_REUSEADDR,&yes,sizeof(yes));
    struct sockaddr_in a={0}; a.sin_family=AF_INET; a.sin_addr.s_addr=htonl(INADDR_LOOPBACK);
    a.sin_port=htons(port);
    if(bind(s,(struct sockaddr *)&a,sizeof(a)) || listen(s,8)) return 2;
    socklen_t n=sizeof(a); getsockname(s,(struct sockaddr *)&a,&n);
    printf("%d\n",ntohs(a.sin_port)); fflush(stdout);
    for(;;) {
        int c=accept(s,NULL,NULL); if(c<0) return 3;
        struct timeval timeout={2,0};
        setsockopt(c,SOL_SOCKET,SO_RCVTIMEO,&timeout,sizeof(timeout));
        setsockopt(c,SOL_SOCKET,SO_SNDTIMEO,&timeout,sizeof(timeout));
        char buffer[65536]={0}; size_t used=0, needed=0; char *headers=NULL;
        while(used<sizeof(buffer)-1) {
            ssize_t got=read(c,buffer+used,sizeof(buffer)-1-used);
            if(got<=0) break;
            used+=(size_t)got; buffer[used]=0;
            if(!headers && (headers=strstr(buffer,"\r\n\r\n"))) {
                char *length=strstr(buffer,"Content-Length:");
                needed=(size_t)(headers-buffer)+4+(length?strtoul(length+15,NULL,10):0);
            }
            if(headers && used>=needed) break;
        }
        if(!headers || used<needed) { close(c); continue; }
        const char *body=HEALTH_BODY;
        if(STRUCTURED && !strncmp(buffer,"POST ",5)) {
            puts("POST"); fflush(stdout);
            const char *payload=headers+4;
            body=strstr(payload,"\"tools\"")?CALL_BODY:
                (strstr(payload,"\"tool_call_id\"") && strstr(payload,"\"5\"")?RESULT_BODY:CHAT_BODY);
        }
        dprintf(c,"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\nContent-Length: %zu\r\n\r\n%s",strlen(body),body);
        close(c);
    }
}
'''


@contextmanager
def compiled(factory, *, structured=False):
    if sys.platform != 'darwin' or not shutil.which('cc'):
        pytest.skip('native Mach-O receipt fixture requires the macOS compiler')
    root = factory.mktemp('native-protocol')
    try:
        source = SOURCE
        for name, value in {'HEALTH_BODY': HEALTH, 'CHAT_BODY': CHAT, 'CALL_BODY': CALL,
                            'RESULT_BODY': RESULT}.items():
            source = source.replace(name, json.dumps(value))
        source = source.replace('STRUCTURED', '1' if structured else '0')
        path = root / 'fixture.c'
        path.write_text(source)
        binary = root / 'fixture-server'
        done = subprocess.run(['cc', str(path), '-o', str(binary)], capture_output=True, text=True, timeout=20)
        assert done.returncode == 0, done.stderr
        yield binary
    finally:
        shutil.rmtree(root)


@pytest.fixture(scope='module')
def native_executable(tmp_path_factory):
    with compiled(tmp_path_factory) as binary:
        yield binary


@pytest.fixture(scope='module')
def probe_executable(tmp_path_factory):
    with compiled(tmp_path_factory, structured=True) as binary:
        yield binary
