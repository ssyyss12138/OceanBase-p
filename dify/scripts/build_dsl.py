"""Build the single delivered Chatflow; node code is versioned beside the YAML."""

from pathlib import Path
import uuid
import yaml

ROOT = Path(__file__).resolve().parents[1]
nodes, edges = [], []


def node(nid, title, kind, **kwargs):
    nodes.append(
        {
            "id": nid,
            "type": "custom",
            "position": {"x": len(nodes) * 330, "y": 250},
            "sourcePosition": "right",
            "targetPosition": "left",
            "data": {"title": title, "desc": "", "type": kind, "version": "1", **kwargs},
        }
    )


def edge(src, dst, handle="source"):
    edges.append(
        {
            "id": f"{src}-{handle}-{dst}",
            "type": "custom",
            "source": src,
            "target": dst,
            "sourceHandle": handle,
            "targetHandle": "target",
        }
    )


def code(nid, title, variables, outputs):
    node(
        nid,
        title,
        "code",
        code_language="python3",
        code=(ROOT / f"dsl/nodes/{nid}.py").read_text(),
        variables=[{"variable": k, "value_selector": v} for k, v in variables.items()],
        outputs={k: {"type": v, "children": None} for k, v in outputs.items()},
        error_strategy="fail-branch",
    )
    edge(nid, "error", "fail-branch")


def http(nid, title, url, selector, key):
    node(
        nid,
        title,
        "http-request",
        method="post",
        url=url,
        headers="Content-Type:application/json",
        params="",
        authorization={"type": "api-key", "config": {"type": "bearer", "api_key": f"{{{{#env.{key}#}}}}"}},
        body={"type": "json", "data": [{"key": "", "type": "text", "value": f"{{{{#{selector}.body#}}}}"}]},
        timeout={"connect": 10, "read": 90, "write": 30},
        ssl_verify=True,
        retry_config={"retry_enabled": False, "max_retries": 1, "retry_interval": 1000},
        error_strategy="default-value",
        default_value=[
            {"key": "body", "type": "string", "value": "{}"},
            {"key": "status_code", "type": "number", "value": 0},
        ],
    )


node(
    "start",
    "进店：输入存档码和行动",
    "start",
    variables=[
        {
            "variable": "player_code",
            "label": "存档码（12—80位，保存它可跨会话继续）",
            "type": "text-input",
            "required": True,
            "max_length": 80,
            "options": [],
        }
    ],
)
code(
    "encode",
    "生成独立轮次",
    {"player_code": ["start", "player_code"], "query": ["sys", "query"]},
    {"body": "string"},
)
http("prepare", "从 PowerContext 召回旧事", "{{#env.GAME_URL#}}/v1/turns/prepare", "encode", "GAME_API_KEY")
code(
    "plan",
    "准备行动识别提示",
    {"body": ["prepare", "body"], "status_code": ["prepare", "status_code"], "model": ["env", "MODEL_NAME"]},
    {"body": "string", "prepared": "string"},
)
http("planner", "Qwen：识别玩家行动", "{{#env.MODEL_BASE_URL#}}/chat/completions", "plan", "MODEL_API_KEY")
code(
    "commit",
    "检查模型结构并提交行动",
    {
        "body": ["planner", "body"],
        "status_code": ["planner", "status_code"],
        "prepared": ["plan", "prepared"],
        "player_code": ["start", "player_code"],
    },
    {"body": "string"},
)
http("save", "校验规则并写入真实记忆", "{{#env.GAME_URL#}}/v1/turns/commit", "commit", "GAME_API_KEY")
code(
    "narrate",
    "将账单和旧事交给老板",
    {"body": ["save", "body"], "status_code": ["save", "status_code"], "model": ["env", "MODEL_NAME"]},
    {"body": "string", "receipt": "string"},
)
http(
    "narrator",
    "Qwen：生成有因果的角色反馈",
    "{{#env.MODEL_BASE_URL#}}/chat/completions",
    "narrate",
    "MODEL_API_KEY",
)
code(
    "format",
    "展示台词、账单与回忆",
    {
        "body": ["narrator", "body"],
        "status_code": ["narrator", "status_code"],
        "receipt": ["narrate", "receipt"],
    },
    {"answer": "string"},
)
node("answer", "老板回应", "answer", answer="{{#format.answer#}}", variables=[])
node(
    "error",
    "服务异常时的明确提示",
    "answer",
    answer="便利店暂时无法完成这一轮。请检查存档码、模型配置和记忆服务后重试。若记忆写入曾中断，下次进入会先恢复已确认的待处理账单；请勿把报错当作交易完成。",
    variables=[],
)
chain = [
    "start",
    "encode",
    "prepare",
    "plan",
    "planner",
    "commit",
    "save",
    "narrate",
    "narrator",
    "format",
    "answer",
]
for a, b in zip(chain, chain[1:]):
    edge(a, b)
vars = []
for name, value, kind in [
    ("GAME_URL", "http://game:8090", "string"),
    ("GAME_API_KEY", "", "secret"),
    ("MODEL_BASE_URL", "", "string"),
    ("MODEL_NAME", "qwen-plus", "string"),
    ("MODEL_API_KEY", "", "secret"),
]:
    vars.append(
        {
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, "causal-store/" + name)),
            "name": name,
            "value": value,
            "value_type": kind,
            "selector": ["env", name],
            "description": "在导入后按 README 配置",
        }
    )
app = {
    "kind": "app",
    "version": "0.4.0",
    "dependencies": [],
    "app": {
        "name": "因果便利店 · 收据会记仇",
        "mode": "advanced-chat",
        "icon": "🏪",
        "icon_background": "#FFF1D6",
        "description": "用一瓶汽水换一个笑话。老板会在下一次进店记起你的选择；三种结局，跨会话延续。",
        "use_icon_as_answer_icon": True,
    },
    "workflow": {
        "conversation_variables": [],
        "environment_variables": vars,
        "features": {
            "opening_statement": "欢迎来到因果便利店。这里的收据会记仇，承诺会过期发芽。\n\n先填写一个你自己保存的随机存档码（12位以上），然后说「看看货架」或「买勇气汽水」。换聊天时用同一码，老板仍记得你。\n\n试玩路线：买汽水 → 讲笑话：具体内容 → 结算；也可以坦白或继续赊账，看看另外两种结局。\n\n存档码是试玩存档的访问凭证，请勿分享；不要输入真实隐私。",
            "suggested_questions": ["看看货架", "买一瓶勇气汽水", "你还记得我以前讲过什么吗？"],
            "suggested_questions_after_answer": {"enabled": False},
            "file_upload": {"enabled": False},
            "retriever_resource": {"enabled": False},
            "sensitive_word_avoidance": {"enabled": False},
            "speech_to_text": {"enabled": False},
            "text_to_speech": {"enabled": False},
        },
        "graph": {"nodes": nodes, "edges": edges, "viewport": {"x": 0, "y": 0, "zoom": 0.45}},
    },
}
(ROOT / "dsl/causal-store.yml").write_text(yaml.safe_dump(app, allow_unicode=True, sort_keys=False))
print("Wrote dsl/causal-store.yml;", len(nodes), "nodes; no plugins required")
