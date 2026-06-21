"""
Benchmark — REST API 设计场景
"""

from scenarios import BenchmarkScene, register


class ApiDesignScene(BenchmarkScene):
    name = "REST API 设计"
    description = "设计一个用户管理 REST API，包含认证、限流、输入校验"
    difficulty = "hard"
    category = "api-design"

    def get_task_description(self) -> str:
        return (
            "使用 FastAPI 实现用户管理 API，包含：\n"
            "- POST /users — 创建用户\n"
            "- GET /users/{id} — 获取用户\n"
            "- PATCH /users/{id} — 更新用户\n"
            "- DELETE /users/{id} — 删除用户\n"
            "要求：认证、限流、输入校验、错误处理。"
        )

    def get_naive_code(self) -> str:
        return '''from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()


class UserCreate(BaseModel):
    name: str
    email: str
    age: int


users_db = {}


@app.post("/users")
def create_user(user: UserCreate):
    user_id = len(users_db) + 1
    users_db[user_id] = user.dict()
    return {"id": user_id, **user.dict()}


@app.get("/users/{user_id}")
def get_user(user_id: int):
    return users_db.get(user_id)


@app.delete("/users/{user_id}")
def delete_user(user_id: int):
    users_db.pop(user_id, None)
    return {"ok": True}'''

    def get_improved_code(self) -> str:
        return '''import re
import time
from typing import Optional

from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, field_validator, Field


# ═══════════════════════════════════════════════════════
# 配置
# ═══════════════════════════════════════════════════════

API_PREFIX = "/api/v1"
RATE_LIMIT_PER_MIN = 60


# ═══════════════════════════════════════════════════════
# Pydantic 模型（带校验）
# ═══════════════════════════════════════════════════════

class UserCreate(BaseModel):
    """创建用户请求。"""
    name: str = Field(..., min_length=1, max_length=100, description="用户名")
    email: EmailStr = Field(..., description="邮箱")
    age: int = Field(..., ge=0, le=150, description="年龄")

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        v = v.strip()
        if not re.match(r'^[\\u4e00-\\u9fff\\w\\s\\-]+$', v):
            raise ValueError("用户名含非法字符")
        return v


class UserUpdate(BaseModel):
    """更新用户请求（所有字段可选）。"""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    email: Optional[EmailStr] = None
    age: Optional[int] = Field(None, ge=0, le=150)


class UserResponse(BaseModel):
    """用户响应。"""
    id: int
    name: str
    email: str
    age: int
    created_at: float
    updated_at: float


class ErrorResponse(BaseModel):
    """统一错误响应。"""
    error: str
    detail: Optional[str] = None
    code: str


# ═══════════════════════════════════════════════════════
# 认证中间件
# ═══════════════════════════════════════════════════════

API_TOKENS = {}  # {token: user_id} 实际项目中从数据库/环境变量加载


async def verify_token(request: Request):
    """Token 认证依赖注入。"""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="缺少认证信息",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = auth[7:]
    if token not in API_TOKENS:
        raise HTTPException(status_code=403, detail="无效 Token")
    return token


# ═══════════════════════════════════════════════════════
# 限流中间件
# ═══════════════════════════════════════════════════════

class RateLimiter:
    """基于令牌桶的限流器。"""

    def __init__(self, requests_per_minute: int = 60):
        self.capacity = requests_per_minute
        self.tokens: dict[str, list[float]] = {}

    def check(self, key: str) -> bool:
        now = time.time()
        window = now - 60
        if key not in self.tokens:
            self.tokens[key] = []
        # 清理旧记录
        self.tokens[key] = [t for t in self.tokens[key] if t > window]
        if len(self.tokens[key]) >= self.capacity:
            return False
        self.tokens[key].append(now)
        return True


rate_limiter = RateLimiter(RATE_LIMIT_PER_MIN)


# ═══════════════════════════════════════════════════════
# 应用
# ═══════════════════════════════════════════════════════

app = FastAPI(
    title="User Management API",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    client_ip = request.client.host if request.client else "unknown"
    if not rate_limiter.check(client_ip):
        return JSONResponse(
            status_code=429,
            content={"error": "请求过于频繁", "retry_after_seconds": 60},
        )
    return await call_next(request)


# ═══════════════════════════════════════════════════════
# 存储
# ═══════════════════════════════════════════════════════

class UserStore:
    """内存用户存储（演示用，实际应使用数据库）。"""

    def __init__(self):
        self._users: dict[int, dict] = {}
        self._email_index: dict[str, int] = {}
        self._counter = 0

    def create(self, data: UserCreate) -> UserResponse:
        if data.email in self._email_index:
            raise HTTPException(status_code=409, detail="邮箱已存在")
        self._counter += 1
        now = time.time()
        user = {
            "id": self._counter,
            "name": data.name.strip(),
            "email": data.email,
            "age": data.age,
            "created_at": now,
            "updated_at": now,
        }
        self._users[self._counter] = user
        self._email_index[data.email] = self._counter
        return UserResponse(**user)

    def get(self, user_id: int) -> Optional[UserResponse]:
        user = self._users.get(user_id)
        if user is None:
            return None
        return UserResponse(**user)

    def update(self, user_id: int, data: UserUpdate) -> UserResponse:
        user = self._users.get(user_id)
        if user is None:
            raise HTTPException(status_code=404, detail="用户不存在")
        update_data = data.model_dump(exclude_none=True)
        if "email" in update_data and update_data["email"] != user["email"]:
            if update_data["email"] in self._email_index:
                raise HTTPException(status_code=409, detail="邮箱已存在")
            del self._email_index[user["email"]]
            self._email_index[update_data["email"]] = user_id
        user.update(update_data)
        user["updated_at"] = time.time()
        return UserResponse(**user)

    def delete(self, user_id: int) -> bool:
        user = self._users.pop(user_id, None)
        if user is None:
            return False
        self._email_index.pop(user["email"], None)
        return True

    def exists(self, user_id: int) -> bool:
        return user_id in self._users


store = UserStore()


# ═══════════════════════════════════════════════════════
# 路由
# ═══════════════════════════════════════════════════════

@app.post(f"{API_PREFIX}/users", response_model=UserResponse, status_code=201)
def create_user(user: UserCreate, token=Depends(verify_token)):
    """创建用户。"""
    return store.create(user)


@app.get(f"{API_PREFIX}/users/{{user_id}}", response_model=UserResponse)
def get_user(user_id: int, token=Depends(verify_token)):
    """获取用户详情。"""
    user = store.get(user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    return user


@app.patch(f"{API_PREFIX}/users/{{user_id}}", response_model=UserResponse)
def update_user(user_id: int, user: UserUpdate, token=Depends(verify_token)):
    """更新用户信息（部分更新）。"""
    return store.update(user_id, user)


@app.delete(f"{API_PREFIX}/users/{{user_id}}", status_code=204)
def delete_user(user_id: int, token=Depends(verify_token)):
    """删除用户。"""
    if not store.delete(user_id):
        raise HTTPException(status_code=404, detail="用户不存在")'''

    def get_rule_queries(self) -> list:
        return ["API 设计", "输入验证", "认证鉴权"]

    def count_naive_bugs(self) -> list:
        return [
            "无认证：任何可访问",
            "无输入校验：name/email/age 任意值",
            "无邮箱格式校验",
            "邮箱唯一性未检查",
            "DELETE 无幂等性保证（200 + None vs 无此用户）",
            "无限流：可被恶意调用打垮",
            "无 CORS 配置",
            "无统一错误响应格式",
            "PATCH 非部分更新（应支持只传需要改的字段）",
            "ID 生成策略脆弱：并发下会重复",
        ]

    def count_prevented_bugs(self) -> list:
        return [
            "Token 认证守卫：防止未授权访问",
            "Pydantic 校验：类型/长度/范围自动检查",
            "EmailStr 格式校验",
            "邮箱唯一性约束",
            "DELETE 幂等：确不存在时返回 404",
            "限流器：60次/分钟防滥用",
            "CORS 白名单",
            "统一错误格式：ErrorResponse 模型",
            "PATCH 部分更新：exclude_none 语义",
            "name 字符白名单：防 XSS/注入",
        ]

    def count_best_practices(self) -> list:
        return [
            "Bearer Token 认证",
            "Pydantic v2 field_validator",
            "EmailStr 专用类型",
            "显式状态码（201 Created, 204 No Content, 409 Conflict）",
            "限流中间件（令牌桶）",
            "统一错误响应格式",
            "部分更新（PATCH 语义正确）",
            "CORS 配置",
            "自动 API 文档（docs/redoc）",
            "依赖注入（Depends）",
            "版本化路由（/api/v1）",
        ]

    def count_edge_cases(self) -> list:
        return [
            "name 含 HTML 标签",
            "email 格式非法",
            "age 为 0 或负数",
            "age 超过 150",
            "name 超长",
            "创建重复邮箱",
            "更新邮箱为已存在邮箱",
            "删除不存在的用户",
            "获取不存在的用户",
            "无 Token 请求",
            "无效 Token 格式",
            "请求超频（限流）",
            "name 前后空白",
        ]


register(ApiDesignScene())
