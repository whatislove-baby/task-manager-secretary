import argparse,sys,threading,time
from pathlib import Path
from .service import BackendService


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--data-dir',required=True);options=parser.parse_args();stop=threading.Event()
    def watch_parent():
        try:sys.stdin.buffer.read()
        finally:stop.set()
    threading.Thread(target=watch_parent,daemon=True).start()
    service=BackendService(Path(options.data_dir),worker=True)
    try:
        while not stop.is_set():
            if not service.operations.run_next():stop.wait(.2)
    finally:service.close()


if __name__=='__main__':main()
