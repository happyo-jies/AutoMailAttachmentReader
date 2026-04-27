
from __future__ import annotations

import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from openai import OpenAI

# =========================
# 配置（可用环境变量覆盖）
# =========================
DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.4")
OPENAI_API_BASE = os.getenv("OPENAI_API_BASE", "http://lightcode-uis.hundsun.com:8080/uis/v1")
OPENAI_API_KEY = os.getenv(
    "OPENAI_API_KEY",
    "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJjYWlxanM0MTg0OCIsImlhdCI6MTc3Mjc4MDY1OH0.ohmoaOVh9s52hQup5v9kk4cL9CXu88_F6aHFPiEgxCA",
)

DEFAULT_HEADERS = {
    "Accept": "*/*",
    "Accept-Encoding": "gzip, deflate, br",
    "User-Agent": "PostmanRuntime-ApipostRuntime/1.1.0",
    "Connection": "keep-alive",
    "Content-Type": "application/json",
}

# skills 默认从项目根目录下的 skills/ 读取（可用 SKILLS_DIR 覆盖）
SKILLS_DIR = Path(os.getenv("SKILLS_DIR", str(Path(__file__).with_name("skills"))))

# 多 agent 历史文件（每个 agent 独立 messages，另有 shared 共享区）
STATE_FILE = Path(__file__).with_name("chat_state.json")

# 防止历史无限增长：最多保留最近 N 条消息（每个 agent）
MAX_MESSAGES = int(os.getenv("MAX_MESSAGES", "120"))


def _build_client() -> OpenAI:
    return OpenAI(
        api_key=OPENAI_API_KEY,
        base_url=OPENAI_API_BASE,
        default_headers=DEFAULT_HEADERS,
    )


def _now_ms() -> int:
    return int(time.time() * 1000)


def _safe_json_load(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _safe_json_write(path: Path, data: Any) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _trim_messages(msgs: list[dict], max_messages: int) -> list[dict]:
    if max_messages <= 0:
        return msgs
    if len(msgs) <= max_messages:
        return msgs
    # 优先保留最早的 system（如果存在），其余截断保留尾部
    systems = [m for m in msgs if m.get("role") == "system"]
    tail = [m for m in msgs if m.get("role") != "system"][-max_messages:]
    return systems[:1] + tail


def _print_help() -> None:
    print(
        "\n指令：\n"
        "  /help                       显示帮助\n"
        "  /exit                       保存并退出\n"
        "  /save                       手动保存\n"
        "  /reset                      清空所有 agent 历史\n"
        "  /agents                     列出 agents\n"
        "  /agent new <name>           新建 agent\n"
        "  /agent switch <name>        切换当前 agent\n"
        "  /agent role <name> <text>   设置 agent 的 system 角色\n"
        "  /agent model <name> <model> 设置 agent 的模型（如 gpt-5.4）\n"
        "  /team                       让所有 agent 依次给出回应（连携工作）\n"
        "  /skills                     列出可用 skills（读取 skills/ 目录）\n"
        "  /skill use <skill>          将 skill 注入到当前 agent（system 追加）\n"
        "  /skill use <skill> @<agent> 将 skill 注入指定 agent\n"
        "\n输入方式：\n"
        "  直接输入文本 -> 发送给当前 agent\n"
        "  @agent 你的问题 -> 发送给指定 agent\n"
    )


def _list_skills() -> list[str]:
    if not SKILLS_DIR.exists():
        return []
    out: list[str] = []
    for p in sorted(SKILLS_DIR.glob("*.md")):
        out.append(p.stem)
    for p in sorted(SKILLS_DIR.glob("*.txt")):
        out.append(p.stem)
    return sorted(set(out))


def _read_skill_text(skill_name: str) -> str | None:
    # 允许 skill_name 不带扩展名
    candidates = [
        SKILLS_DIR / f"{skill_name}.md",
        SKILLS_DIR / f"{skill_name}.txt",
    ]
    for p in candidates:
        if p.exists() and p.is_file():
            try:
                return p.read_text(encoding="utf-8")
            except Exception:
                return None
    return None


@dataclass
class Agent:
    name: str
    model: str = DEFAULT_MODEL
    system_prompt: str = ""
    messages: list[dict] = field(default_factory=list)

    def ensure_system(self) -> None:
        if self.system_prompt and not any(m.get("role") == "system" for m in self.messages):
            self.messages.insert(0, {"role": "system", "content": self.system_prompt})

    def append_user(self, text: str) -> None:
        self.ensure_system()
        self.messages.append({"role": "user", "content": text})
        self.messages = _trim_messages(self.messages, MAX_MESSAGES)

    def append_assistant(self, text: str) -> None:
        self.messages.append({"role": "assistant", "content": text})
        self.messages = _trim_messages(self.messages, MAX_MESSAGES)

    def inject_skill(self, skill_name: str, skill_text: str) -> None:
        self.ensure_system()
        payload = (
            "【Skill 注入】\n"
            f"skill: {skill_name}\n\n"
            f"{skill_text.strip()}\n"
        )
        self.messages.append({"role": "system", "content": payload})
        self.messages = _trim_messages(self.messages, MAX_MESSAGES)


@dataclass
class OrchestratorState:
    active: str
    shared: dict[str, Any] = field(default_factory=dict)
    agents: dict[str, Agent] = field(default_factory=dict)
    updated_at_ms: int = field(default_factory=_now_ms)

    def to_json(self) -> dict[str, Any]:
        return {
            "active": self.active,
            "shared": self.shared,
            "updated_at_ms": self.updated_at_ms,
            "agents": {
                name: {
                    "name": ag.name,
                    "model": ag.model,
                    "system_prompt": ag.system_prompt,
                    "messages": ag.messages,
                }
                for name, ag in self.agents.items()
            },
        }

    @staticmethod
    def from_json(data: Any) -> OrchestratorState:
        if not isinstance(data, dict):
            return OrchestratorState(active="main", agents={"main": Agent(name="main")})
        agents_raw = data.get("agents") if isinstance(data.get("agents"), dict) else {}
        agents: dict[str, Agent] = {}
        for name, a in agents_raw.items():
            if not isinstance(a, dict):
                continue
            ag = Agent(
                name=str(a.get("name") or name),
                model=str(a.get("model") or DEFAULT_MODEL),
                system_prompt=str(a.get("system_prompt") or ""),
                messages=a.get("messages") if isinstance(a.get("messages"), list) else [],
            )
            # 清洗 messages
            cleaned: list[dict] = []
            for m in ag.messages:
                if (
                    isinstance(m, dict)
                    and m.get("role") in {"system", "user", "assistant", "tool"}
                    and isinstance(m.get("content"), str)
                ):
                    cleaned.append({"role": m["role"], "content": m["content"]})
            ag.messages = _trim_messages(cleaned, MAX_MESSAGES)
            agents[name] = ag

        active = str(data.get("active") or "main")
        if not agents:
            agents = {"main": Agent(name="main")}
            active = "main"
        if active not in agents:
            active = next(iter(agents.keys()))
        st = OrchestratorState(
            active=active,
            shared=data.get("shared") if isinstance(data.get("shared"), dict) else {},
            agents=agents,
            updated_at_ms=int(data.get("updated_at_ms") or _now_ms()),
        )
        return st


def _load_state() -> OrchestratorState:
    data = _safe_json_load(STATE_FILE)
    return OrchestratorState.from_json(data)


def _save_state(state: OrchestratorState) -> None:
    state.updated_at_ms = _now_ms()
    _safe_json_write(STATE_FILE, state.to_json())


def _stream_chat_once(client: OpenAI, model: str, messages: list[dict]) -> str:
    chat_completion = client.chat.completions.create(
        messages=messages,
        model=model,
        stream=True,
    )
    reply = ""
    for chunk in chat_completion:
        if not hasattr(chunk, "choices") or not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        content = getattr(delta, "content", None)
        if content:
            print(content, end="", flush=True)
            reply += content
    print()
    return reply


_TARGET_RE = re.compile(r"^\s*@([a-zA-Z0-9_\-]+)\s+(.*)$", re.S)


def _parse_targeted_input(text: str) -> tuple[str | None, str]:
    m = _TARGET_RE.match(text)
    if not m:
        return None, text
    return m.group(1), m.group(2).strip()


def _ensure_agent(state: OrchestratorState, name: str) -> Agent:
    if name not in state.agents:
        state.agents[name] = Agent(name=name)
    return state.agents[name]


def _handle_team_mode(client: OpenAI, state: OrchestratorState, user_text: str) -> None:
    # shared 区可以放协同信息（例如需求摘要/约束/上下文）
    # 这里采用最简连携：同一条用户输入，依次给每个 agent 响应
    for name in list(state.agents.keys()):
        ag = state.agents[name]
        ag.append_user(user_text)
        print(f"\n[{name}]：", end="", flush=True)
        reply = _stream_chat_once(client, ag.model, ag.messages)
        ag.append_assistant(reply)


def main() -> None:
    client = _build_client()
    state = _load_state()

    print(f"多 Agent 会话已加载：{STATE_FILE}")
    print(f"当前 agent：{state.active}；skills 目录：{SKILLS_DIR}")
    print("输入 /help 查看指令。")

    while True:
        try:
            user_text = input("\n你：").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已退出。")
            _save_state(state)
            break

        if not user_text:
            continue

        cmd = user_text.strip()
        low = cmd.lower()

        if low in {"/help", "help", "?"}:
            _print_help()
            continue

        if low in {"/exit", "exit", "quit", "/quit"}:
            _save_state(state)
            print(f"已保存：{STATE_FILE}")
            break

        if low == "/save":
            _save_state(state)
            print(f"已保存：{STATE_FILE}")
            continue

        if low == "/reset":
            state = OrchestratorState(active="main", agents={"main": Agent(name="main")})
            try:
                if STATE_FILE.exists():
                    STATE_FILE.unlink()
            except Exception:
                pass
            print("已清空所有 agent 历史。")
            continue

        if low == "/agents":
            names = list(state.agents.keys())
            print("Agents：")
            for n in names:
                mark = "*" if n == state.active else " "
                ag = state.agents[n]
                print(f" {mark} {n} (model={ag.model}, messages={len(ag.messages)})")
            continue

        if low.startswith("/agent new "):
            name = cmd[len("/agent new ") :].strip()
            if not name:
                print("用法：/agent new <name>")
                continue
            _ensure_agent(state, name)
            state.active = name
            _save_state(state)
            print(f"已创建并切换到：{name}")
            continue

        if low.startswith("/agent switch "):
            name = cmd[len("/agent switch ") :].strip()
            if name not in state.agents:
                print(f"未找到 agent：{name}。可用 /agents 查看。")
                continue
            state.active = name
            _save_state(state)
            print(f"已切换到：{name}")
            continue

        if low.startswith("/agent role "):
            rest = cmd[len("/agent role ") :].strip()
            parts = rest.split(" ", 1)
            if len(parts) != 2:
                print("用法：/agent role <name> <text>")
                continue
            name, role_text = parts[0].strip(), parts[1].strip()
            ag = _ensure_agent(state, name)
            ag.system_prompt = role_text
            # 重置 system 插入逻辑：下次会自动补到 messages[0]
            # 这里不强行覆盖历史已有 system，避免破坏上下文
            _save_state(state)
            print(f"已设置 {name} 的角色描述（system）。")
            continue

        if low.startswith("/agent model "):
            rest = cmd[len("/agent model ") :].strip()
            parts = rest.split(" ", 1)
            if len(parts) != 2:
                print("用法：/agent model <name> <model>")
                continue
            name, model_name = parts[0].strip(), parts[1].strip()
            if not name or not model_name:
                print("用法：/agent model <name> <model>")
                continue
            ag = _ensure_agent(state, name)
            ag.model = model_name
            _save_state(state)
            print(f"已设置 {name} 的模型：{model_name}")
            continue

        if low == "/team":
            print("进入连携模式：所有 agents 将依次回应同一条输入。")
            team_input = input("团队任务：").strip()
            if not team_input:
                continue
            _handle_team_mode(client, state, team_input)
            _save_state(state)
            continue

        if low == "/skills":
            skills = _list_skills()
            if not skills:
                print(f"未找到 skills。请在目录下放入 .md/.txt：{SKILLS_DIR}")
            else:
                print("可用 skills：")
                for s in skills:
                    print(f" - {s}")
            continue

        if low.startswith("/skill use "):
            rest = cmd[len("/skill use ") :].strip()
            target_agent = None
            if "@" in rest:
                # 允许形如：/skill use xxx @agent
                segs = rest.split()
                skill_name = segs[0].strip() if segs else ""
                for seg in segs[1:]:
                    if seg.startswith("@"):
                        target_agent = seg[1:].strip()
                        break
            else:
                skill_name = rest

            if not skill_name:
                print("用法：/skill use <skill> 或 /skill use <skill> @<agent>")
                continue

            skill_text = _read_skill_text(skill_name)
            if not skill_text:
                print(f"未找到 skill：{skill_name}（在 {SKILLS_DIR} 下查找 .md/.txt）")
                continue

            name = target_agent or state.active
            ag = _ensure_agent(state, name)
            ag.inject_skill(skill_name, skill_text)
            _save_state(state)
            print(f"已将 skill 注入到 agent：{name}")
            continue

        # ============ 常规对话 ============
        target, content = _parse_targeted_input(user_text)
        if target:
            ag = _ensure_agent(state, target)
        else:
            ag = _ensure_agent(state, state.active)

        if not content:
            continue

        ag.append_user(content)
        print(f"{ag.name}：", end="", flush=True)
        reply = _stream_chat_once(client, ag.model, ag.messages)
        ag.append_assistant(reply)
        _save_state(state)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"\n运行异常：{e}", file=sys.stderr)
        sys.exit(1)