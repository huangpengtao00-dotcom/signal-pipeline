#!/usr/bin/env python3
"""信号管道编排器 —— 单人系统，零依赖，一本台账。

用法:
  python3 pipeline.py collect            # 采集今日数据（幂等，已有则跳过；--force 重采）
  python3 pipeline.py daily              # 生成短视频/内容选题日报（需 claude CLI）
  python3 pipeline.py weekly             # 生成信号周报草稿（需 claude CLI）
  python3 pipeline.py patterns           # 更新选题漏斗（需 claude CLI）
  python3 pipeline.py samples            # 生成公开样本监控（需 claude CLI）
  python3 pipeline.py run                # 日常入口：collect + daily + samples；周一另跑 weekly + patterns
  python3 pipeline.py status             # 查看台账最近记录与缺口

设计约束（为什么这么简单）：单人维护的系统，维护成本是第一红线——
无数据库（文件即状态）、无队列（cron 即调度）、无框架（stdlib 即依赖）。
每次运行写台账 ledger.jsonl：做了什么、成功与否、产物在哪，系统自身可追问。
"""
import json
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).parent
TODAY = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
ISOWEEK = datetime.now(timezone.utc).astimezone().strftime("%G-W%V")
LEDGER = ROOT / "ledger.jsonl"


def log(step: str, ok: bool, detail: str = "", output: str = ""):
    rec = {"ts": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
           "step": step, "ok": ok, "detail": detail, "output": output}
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(("[ok] " if ok else "[fail] ") + step + (f" · {detail}" if detail else ""))


def collect(force: bool = False):
    out = ROOT / "data" / f"{TODAY}.json"
    if out.exists() and not force:
        log("collect", True, "今日已采集，跳过（--force 重采）", str(out))
        return
    r = subprocess.run([sys.executable, str(ROOT / "collect.py")],
                       capture_output=True, text=True)
    log("collect", r.returncode == 0,
        (r.stdout + r.stderr).strip()[-200:], str(out) if r.returncode == 0 else "")
    if r.returncode != 0:
        sys.exit(1)


def _claude(prompt_file: str, data_files: list, out_path: Path, step: str):
    """prompt 模板 + 数据 JSON 拼成完整提示，交给 claude -p，stdout 落盘。"""
    prompt = (ROOT / "prompts" / prompt_file).read_text(encoding="utf-8")
    blobs = []
    for df in data_files:
        p = Path(df)
        if p.exists():
            blobs.append(f"<data file=\"{p.name}\">\n{p.read_text(encoding='utf-8')}\n</data>")
    if not blobs:
        log(step, False, "无可用数据文件，先跑 collect")
        return
    extra = ""
    if step == "patterns" and (ROOT / "out" / "patterns.md").exists():
        extra = ("\n<current-patterns>\n"
                 + (ROOT / "out" / "patterns.md").read_text(encoding="utf-8")
                 + "\n</current-patterns>")
    full = f"{prompt}\n\n今天是 {TODAY}。只输出目标 markdown 内容本身，不要多余说明。\n\n" \
           + "\n".join(blobs) + extra
    try:
        r = subprocess.run(["claude", "-p", full], capture_output=True,
                           text=True, timeout=600)
    except FileNotFoundError:
        log(step, False, "找不到 claude CLI（生成步骤需要 Claude Code 已登录）")
        return
    if r.returncode != 0 or not r.stdout.strip():
        log(step, False, r.stderr.strip()[-200:])
        return
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(r.stdout.strip() + "\n", encoding="utf-8")
    log(step, True, "", str(out_path))


def week_data(days: int = 7) -> list[Path]:
    now = datetime.now(timezone.utc).astimezone()
    return [ROOT / "data" / f"{(now - timedelta(days=i)).strftime('%Y-%m-%d')}.json"
            for i in range(days)]


def status():
    if not LEDGER.exists():
        print("台账为空——还没跑过。先：python3 pipeline.py run")
        return
    lines = LEDGER.read_text(encoding="utf-8").strip().splitlines()[-12:]
    for ln in lines:
        r = json.loads(ln)
        print(f"{r['ts']}  {'✓' if r['ok'] else '✗'} {r['step']:9s} {r.get('output','') or r.get('detail','')}")
    have = sorted(p.stem for p in (ROOT / "data").glob("*.json"))
    print(f"\n数据覆盖：{len(have)} 天（{have[0]} → {have[-1]}）" if have else "\n无数据")


def main():
    args = sys.argv[1:]
    cmd = args[0] if args else "run"
    force = "--force" in args
    today_json = [ROOT / "data" / f"{TODAY}.json"]
    if cmd == "collect":
        collect(force)
    elif cmd == "daily":
        _claude("daily-douyin.md", today_json,
                ROOT / "out" / "daily" / f"{TODAY}-douyin.md", "daily")
    elif cmd == "weekly":
        _claude("weekly-signals.md", week_data(),
                ROOT / "out" / "weekly" / f"{ISOWEEK}-signals.md", "weekly")
    elif cmd == "patterns":
        _claude("patterns.md", sorted((ROOT / "data").glob("*.json")),
                ROOT / "out" / "patterns.md", "patterns")
    elif cmd == "samples":
        _claude("samples.md", today_json,
                ROOT / "out" / "samples" / f"{TODAY}-samples.md", "samples")
    elif cmd == "run":
        collect(force)
        _claude("daily-douyin.md", today_json,
                ROOT / "out" / "daily" / f"{TODAY}-douyin.md", "daily")
        _claude("samples.md", today_json,
                ROOT / "out" / "samples" / f"{TODAY}-samples.md", "samples")
        if datetime.now(timezone.utc).astimezone().weekday() == 0:  # 周一（按本地时区）
            _claude("weekly-signals.md", week_data(),
                    ROOT / "out" / "weekly" / f"{ISOWEEK}-signals.md", "weekly")
            _claude("patterns.md", sorted((ROOT / "data").glob("*.json")),
                    ROOT / "out" / "patterns.md", "patterns")
    elif cmd == "status":
        status()
    else:
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
