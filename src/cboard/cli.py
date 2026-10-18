from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

from .config import config_path, load_config
from .deletion import CodexSessionDeleter
from .models import ThreadKey
from .renaming import CodexSessionRenamer
from .services import Board
from .state import StateError, StateStore
from .terminals.command import TerminalUnavailable, resume_spec
from .terminals import terminal_launcher


async def inspect_board(args, config):
    board = Board(config)
    try:
        await board.refresh()
        if args.command == "list":
            state = StateStore(config.state_dir).read()
            tasks, counts = board.visible(state, account=args.account, query=args.search, status=args.status, show_hidden=args.hidden)
            result = {
                "tasks": [t.to_json() for t in tasks], "counts": dict(counts),
                "accounts": [{"id": a.id, "label": a.label, "complete": board.snapshots[a.id].complete, "stale": board.snapshots[a.id].stale, "error": board.snapshots[a.id].error} for a in config.accounts],
            }
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                if not config.accounts:
                    print("등록된 Cboard 계정이 없습니다. 계정 추가: cnew NAME / 조회: clist · cusage. 보드 연결 설정: " + str(config_path()))
                for task in tasks:
                    print(f"{task.key.encoded}\t{task.status.value}\t{task.title}\t{task.cwd}")
                for account in result["accounts"]:
                    if account["error"]:
                        print(f"{account['label']}: {account['error']}", file=sys.stderr)
            return 0 if any(s.complete and not s.stale for s in board.snapshots.values()) else 1
        key = ThreadKey.decode(args.key)
        if key.encoded not in board.tasks:
            raise ValueError("대화가 원래 목록에 없습니다. 계정과 대화 ID를 확인하세요")
        source = board.sources[key.account_id]
        if args.command == "detail":
            detail = await source.detail(key.thread_id)
            print(json.dumps(asdict(detail), ensure_ascii=False, indent=2))
        elif args.command in {"open", "_resume"}:
            if board.snapshots[key.account_id].stale:
                raise ValueError("계정 연결을 확인할 수 없습니다")
            task = await source.read_task(key.thread_id)
            spec = resume_spec(config, task)
            if args.command == "_resume":
                await board.close()
                os.execve(spec.argv[0], list(spec.argv), spec.environment())
            elif args.copy:
                print(spec.command)
            else:
                print(await terminal_launcher(config.state_dir, args.config).open(spec))
        return 0
    finally:
        await board.close()


def main():
    parser = argparse.ArgumentParser(description="계정 전체의 Codex Agent Command Center")
    parser.add_argument("--config", type=Path)
    commands = parser.add_subparsers(dest="command")
    listing = commands.add_parser("list", help="대화 목록 조회")
    listing.add_argument("--json", action="store_true")
    listing.add_argument("--account", default="all")
    listing.add_argument("--status", default="All")
    listing.add_argument("--search", default="")
    listing.add_argument("--hidden", action="store_true")
    for name in ["detail", "open", "_resume"]:
        cmd = commands.add_parser(name, help="대화 상세 조회" if name == "detail" else "대화 재개")
        cmd.add_argument("key", help="account_id/thread_id")
        if name == "open":
            cmd.add_argument("--copy", action="store_true", help="실행 대신 재개 명령 출력")
    commands.add_parser("doctor", help="계정 연결 상태 확인")
    commands.add_parser("init", help="기본 계정 설정 파일 생성")
    args = parser.parse_args()
    try:
        config = load_config(args.config)
        if args.command == "init":
            path = args.config or config_path()
            if path.exists():
                raise ValueError(f"설정 파일이 이미 있습니다: {path}")
            path.parent.mkdir(parents=True, exist_ok=True)
            text = f'schema_version = 1\ncodex_executable = {json.dumps(str(config.executable))}\n'
            if not config.accounts:
                text += 'accounts = []\n'
            for account in config.accounts:
                text += f'\n[[accounts]]\nid = {json.dumps(account.id)}\nlabel = {json.dumps(account.label)}\ncodex_home = {json.dumps(str(account.home))}\nenabled = true\n'
            path.write_text(text, encoding="utf-8")
            print(path)
        elif args.command == "doctor":
            from .sources.codex import CodexSource
            async def doctor():
                async def check(account):
                    source = CodexSource(account, config.timeout)
                    try:
                        snapshot = await source.refresh()
                        return {"account": account.id, "socket": str(account.socket_path), "connected": not snapshot.stale, "tasks": sum(not t.is_child for t in snapshot.tasks), "error": snapshot.error}
                    finally:
                        await source.close()
                print(json.dumps(await asyncio.gather(*(check(a) for a in config.accounts)), ensure_ascii=False, indent=2))
            asyncio.run(doctor())
        elif args.command:
            raise SystemExit(asyncio.run(inspect_board(args, config)))
        else:
            from .ui.app import CboardApp
            CboardApp(config, launcher=terminal_launcher(config.state_dir, args.config), deleter=CodexSessionDeleter(config), renamer=CodexSessionRenamer(config)).run()
    except (ValueError, OSError, StateError, StopIteration, TerminalUnavailable) as exc:
        parser.exit(1, f"cboard: {exc}\n")
    except KeyboardInterrupt:
        pass
