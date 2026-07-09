"""
Minecraft 服务器启动器

基于 start.bat 逻辑，用 Python 重新实现。
修改下方路径配置后直接运行即可。
"""

import os
import sys

from main import start_minecraft_server, start_server_interactive


# ===== 配置区（根据你的实际路径修改） =====

# Java 可执行文件路径
JAVA_PATH = os.path.join(os.path.dirname(__file__), "jdk-21.0.9", "bin", "java.exe")

# 服务端核心 jar 文件路径
JAR_PATH = os.path.join(os.path.dirname(__file__), "leaves.jar")

# 内存配置
MIN_MEM = "1G"
MAX_MEM = "16G"

# 是否启用控制台交互（支持输入服务器指令）
INTERACTIVE = True


def main():
    if not os.path.isfile(JAVA_PATH):
        print(f"[错误] 未找到 Java: {JAVA_PATH}")
        sys.exit(1)
    if not os.path.isfile(JAR_PATH):
        print(f"[错误] 未找到服务端核心: {JAR_PATH}")
        sys.exit(1)

    print("=" * 50)
    print("  Minecraft Leaves Server 1.21.1")
    print(f"  Java 版本: 21.0.9")
    print(f"  分配内存:  最小 {MIN_MEM} / 最大 {MAX_MEM}")
    print("=" * 50)
    print()

    try:
        if INTERACTIVE:
            exit_code = start_server_interactive(
                java_path=JAVA_PATH,
                jar_path=JAR_PATH,
                min_mem=MIN_MEM,
                max_mem=MAX_MEM,
            )
        else:
            exit_code = start_minecraft_server(
                java_path=JAVA_PATH,
                jar_path=JAR_PATH,
                min_mem=MIN_MEM,
                max_mem=MAX_MEM,
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
