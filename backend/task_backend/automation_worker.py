"""One bounded unattended mail tick; independent of Electron's stdin lifetime."""
import argparse
import sys
from pathlib import Path


def run_once(directory, factory=None):
    if factory is None:
        from .service import BackendService
        factory = BackendService
    directory = Path(directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    # worker=True must not pause foreground jobs or spawn a desktop-owned worker.
    service = factory(directory, worker=True)
    try:
        return service.automation.tick()
    finally:
        service.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', required=True)
    parser.add_argument('--once', action='store_true', required=True)
    args = parser.parse_args()
    try:
        run_once(args.data_dir)
        return 0
    except Exception:
        # Never print third-party exceptions, mail contents or auth configuration.
        print('邮件整理后台未完成，请在应用中查看状态并检查连接。', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
