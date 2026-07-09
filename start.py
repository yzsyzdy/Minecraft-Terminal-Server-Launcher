"""
Minecraft 服务器启动器

从 config.json 读取配置并启动服务器。
首次运行会自动生成默认 config.json。
"""

import os
import sys

from main import (
    load_config,
    save_config,
    resolve_java,
    start_minecraft_server,
    start_server_interactive,
)


def main():
    project_dir = os.path.dirname(os.path.abspath(__file__))

    # 加载配置，不存在则自动创建默认文件
    config = load_config(project_dir)
    save_config(config, project_dir)

    jar_path = config["jar_path"]
    jar_abs = os.path.abspath(os.path.join(project_dir, jar_path))
    if not os.path.isfile(jar_abs):
        print(f"[错误] 未找到服务端核心: {jar_path}")
        print(f"       期望路径: {jar_abs}")
        print("  请修改 config.json 中的 jar_path。")
        sys.exit(1)

    # 解析 Java 路径
    try:
        java_abs = resolve_java(
            configured_path=config.get("java_path"),
            storage_dir=project_dir,
        )
    except FileNotFoundError as e:
        print(f"[错误] {e}")
        sys.exit(1)

    min_mem = config.get("min_mem", "1G")
    max_mem = config.get("max_mem", "16G")
    interactive = config.get("interactive", True)

    print("=" * 50)
    print("  Minecraft Leaves Server 1.21.1")
    print(f"  Java:     {java_abs}")
    print(f"  核心:     {jar_abs}")
    print(f"  内存:     最小 {min_mem} / 最大 {max_mem}")
    print("=" * 50)
    print()

    try:
        if interactive:
            exit_code = start_server_interactive(
                java_path=java_abs,
                jar_path=jar_abs,
                min_mem=min_mem,
                max_mem=max_mem,
            )
        else:
            exit_code = start_minecraft_server(
                java_path=java_abs,
                jar_path=jar_abs,
                min_mem=min_mem,
                max_mem=max_mem,
            )
    except FileNotFoundError as e:
        print(f"[错误] {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        print("\n[关闭] 用户中断")
        sys.exit(0)

    print(f"\n[完成] 服务器已关闭，退出码: {exit_code}")
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
