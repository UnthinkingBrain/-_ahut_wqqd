# -*- coding: utf-8 -*-
# @Author：Spance
# @Email: wqqd@spance.xyz
# @Desc: 配置加载器（纯标准库，main.py / main_sync.py 共用，无第三方依赖）
#
# 用户配置优先级（从高到低）：
#   1) 命令行显式指定的配置文件（main.py 的 -f/--file 参数）
#   2) 脚本同目录下的 users.json（格式见 users.example.json）
#   3) 交互式输入（无配置文件时自动进入，可选择保存为 users.json 供下次复用）
#
# 安全提示：users.json 含明文密码，已被 .gitignore 排除，禁止提交或外传。

import json
import os
import sys

DEFAULT_FILENAME = "users.json"

# 允许出现在配置文件中的用户字段（与 main*.py 中的 User dataclass 对齐）
# student_Id 为源码字段名，配置文件推荐写小写 student_id，两者等价
_ALLOWED_FIELDS = {"student_id", "student_Id", "studentId", "username", "password",
                   "is_encrypted", "latitude", "longitude"}


def _normalize_user(raw, index):
    """
    校验并归一化单个用户配置。

    :param raw: 配置文件中的单个用户 dict
    :param index: 在列表中的序号（用于报错提示）
    :return: 归一化后的 dict（学号键统一为 student_id，非法字段已过滤）
    """
    if not isinstance(raw, dict):
        raise ValueError(f"第 {index + 1} 个用户配置必须是对象(dict)，实际为 {type(raw).__name__}")
    # 学号：student_id / student_Id / studentId 三种写法都认
    student_id = raw.get("student_id", raw.get("student_Id", raw.get("studentId")))
    if student_id in (None, ""):
        raise ValueError(f"第 {index + 1} 个用户缺少学号(student_id)")
    try:
        student_id = int(student_id)
    except (TypeError, ValueError):
        raise ValueError(f"第 {index + 1} 个用户的学号必须是纯数字，实际为 {student_id!r}")

    user = {"student_id": student_id}
    for key in ("username", "password", "is_encrypted", "latitude", "longitude"):
        if key in raw and raw[key] not in (None, ""):
            user[key] = raw[key]
    # 容错：下划线开头的键视为注释静默忽略，其他未知字段提示后忽略
    unknown = {k for k in raw if k not in _ALLOWED_FIELDS and not k.startswith("_")}
    if unknown:
        print(f"[config] 提示：第 {index + 1} 个用户存在未知字段 {sorted(unknown)}，已忽略")
    return user


def load_users_from_file(path):
    """
    从 JSON 文件加载用户配置。

    :param path: 配置文件路径
    :return: 归一化后的 dict 列表；文件不存在返回 None（与"格式错误抛异常"区分开）
    支持两种顶层结构：{"users": [...]} 或直接 [...]
    """
    if not os.path.isfile(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict):
        data = data.get("users", [])
    if not isinstance(data, list):
        raise ValueError(f"{path} 的顶层结构应是 {{'users': [...]}} 或 [...]")
    users = [_normalize_user(raw, i) for i, raw in enumerate(data)]
    if not users:
        raise ValueError(f"{path} 中没有找到任何用户配置")
    return users


def save_users_to_file(path, users):
    """把归一化后的用户列表保存为 {"users": [...]} 结构"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"users": users}, f, ensure_ascii=False, indent=2)


def interactive_collect_users(base_dir=None):
    """
    交互式收集用户配置（支持多人），返回归一化 dict 列表。

    输入完成后可选择保存到脚本同目录的 users.json，下次运行免输入。
    """
    print("=" * 60)
    print("未检测到 users.json 配置文件，进入交互式配置")
    print("提示：若考勤系统密码为初始密码，密码处直接回车即可")
    print("=" * 60)
    users = []
    while True:
        raw_id = input(f"请输入第 {len(users) + 1} 位用户的学号后回车（直接回车结束输入）：").strip()
        if not raw_id:
            if not users:
                print("至少需要一位用户，请重新输入")
                continue
            break
        try:
            student_id = int(raw_id)
        except ValueError:
            print(f"学号必须是纯数字，收到的是 {raw_id!r}，请重新输入")
            continue
        password = input("请输入密码（初始密码直接回车）：").strip() or "Ahgydx@920"
        username = input("请输入姓名（可直接回车跳过）：").strip()
        user = {"student_id": student_id, "password": password}
        if username:
            user["username"] = username
        users.append(user)
        print(f"已添加：{user.get('username') or student_id}")

    answer = input(f"\n是否将以上 {len(users)} 位用户保存到 {DEFAULT_FILENAME}，下次免输入？(Y/n)：").strip().lower()
    if answer in ("", "y", "yes"):
        if base_dir is None:
            base_dir = os.path.dirname(os.path.abspath(sys.argv[0])) or "."
        target = os.path.join(base_dir, DEFAULT_FILENAME)
        try:
            save_users_to_file(target, users)
            print(f"[config] 已保存到 {target}")
            print("[config] 注意：该文件含明文密码，已被 .gitignore 排除，请勿外传或提交到仓库")
        except OSError as e:
            print(f"[config] 保存失败（不影响本次签到）：{e}")
    return users


def to_user_kwargs(d):
    """
    把归一化后的用户 dict 转换为 User dataclass 的关键字参数。

    字段名与 main*.py 中的 User dataclass 完全对齐（学号字段为 student_Id）。
    """
    kwargs = {
        "student_Id": d["student_id"],
        "password": d.get("password") or "Ahgydx@920",
    }
    for key in ("username", "latitude", "longitude", "is_encrypted"):
        if key in d:
            kwargs[key] = d[key]
    return kwargs


def resolve_users(config_path=None, base_dir=None):
    """
    配置解析主入口，按优先级返回归一化用户 dict 列表：
      config_path(显式指定) > base_dir/users.json > 交互式输入

    :param config_path: 显式指定的配置文件路径（可为 None）
    :param base_dir: 用于定位默认 users.json 的目录（通常是脚本所在目录）
    """
    if config_path:
        users = load_users_from_file(config_path)
        if users is None:
            raise FileNotFoundError(f"指定的配置文件不存在：{config_path}")
        print(f"[config] 已从 {config_path} 加载 {len(users)} 位用户")
        return users

    if base_dir is None:
        base_dir = os.path.dirname(os.path.abspath(sys.argv[0])) or "."
    default_path = os.path.join(base_dir, DEFAULT_FILENAME)
    users = load_users_from_file(default_path)
    if users is not None:
        print(f"[config] 检测到 {default_path}，已加载 {len(users)} 位用户（如需交互输入请删除或改名该文件）")
        return users

    try:
        return interactive_collect_users(base_dir=base_dir)
    except (KeyboardInterrupt, EOFError):
        # 交互中途 Ctrl+C / 输入流结束：给出友好提示后退出，不甩 traceback
        print("\n[config] 交互输入已取消，未执行签到。可创建 users.json 后重试（格式见 users.example.json）")
        raise SystemExit(1)
