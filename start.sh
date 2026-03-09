#!/bin/bash
# 闲鱼自动回复系统启动脚本

cd "$(dirname "$0")"

# 日志文件路径
LOG_DIR="logs"
LOG_FILE="$LOG_DIR/startup.log"

# 创建日志目录
mkdir -p "$LOG_DIR"

echo "========================================"
echo "  闲鱼自动回复系统"
echo "========================================"

# 检查是否已有实例在运行
if pgrep -f "Start.py" > /dev/null; then
    echo "检测到程序已在运行"
    echo ""
    read -p "是否要重启? (y/n): " choice
    if [ "$choice" = "y" ] || [ "$choice" = "Y" ]; then
        echo "正在停止现有进程..."
        pkill -f "Start.py"
        sleep 2
    else
        echo "保持现有进程运行"
        echo "Web管理界面: http://localhost:8090"
        exit 0
    fi
fi

# 启动程序
echo "正在启动..."

# 将输出重定向到日志文件，并在后台运行
# nohup 确保终端关闭后进程继续运行
# 输出同时写入 startup.log 和 /dev/null（避免终端输出）
nohup ./venv/bin/python -u Start.py >> "$LOG_FILE" 2>&1 &

# 获取进程 PID
PID=$!
echo "进程 PID: $PID"

# 等待启动
sleep 3

# 检查是否启动成功
if pgrep -f "Start.py" > /dev/null; then
    echo "========================================"
    echo "  启动成功!"
    echo "  Web管理界面: http://localhost:8090"
    echo "  日志文件: $LOG_FILE"
    echo "  查看日志: tail -f $LOG_FILE"
    echo "  停止服务: pkill -f 'Start.py'"
    echo "========================================"
else
    echo "启动失败，请检查日志: $LOG_FILE"
    tail -20 "$LOG_FILE"
    exit 1
fi