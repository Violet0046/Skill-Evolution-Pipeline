#!/bin/bash
# =============================================================================
# 挂载远程 Viking 目录到本地
#
# 用法: ./scripts/mount_viking.sh
# =============================================================================

set -e

# 配置
TARGET='/home/session_skill_mapping'
REMOTE='root@10.89.246.60:/home/session_skill_mapping/'
HOST='10.89.246.60'

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# 检查依赖
check_deps() {
    if ! command -v sshfs &> /dev/null; then
        log_error "sshfs 未安装，请先安装: sudo yum install fuse-sshfs"
        exit 1
    fi

    if ! command -v sshpass &> /dev/null; then
        log_error "sshpass 未安装，请先安装: sudo yum install sshpass"
        exit 1
    fi
}

# 检查是否已挂载
is_mounted() {
    mount | grep -q "$TARGET" && return 0 || return 1
}

# 尝试 SSH 连接
check_ssh() {
    log_info "检查 SSH 连接..."
    if ! sshpass -p 'Y!dj9462' ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 root@$HOST "echo ok" &>/dev/null; then
        log_error "无法连接到 $HOST，请检查网络和凭据"
        exit 1
    fi
    log_info "SSH 连接正常 ✓"
}

# 卸载旧挂载
umount_old() {
    if is_mounted; then
        log_warn "检测到旧挂载，先卸载..."
        sudo fusermount -u "$TARGET" 2>/dev/null || sudo umount "$TARGET" 2>/dev/null || true
        sleep 1
    fi
}

# 创建挂载点
prepare_mountpoint() {
    if [ ! -d "$TARGET" ]; then
        log_info "创建挂载点: $TARGET"
        sudo mkdir -p "$TARGET"
        sudo chmod 755 "$TARGET"
    fi
}

# 执行挂载
do_mount() {
    log_info "正在挂载 $REMOTE -> $TARGET ..."
    echo 'Y!dj9462' | sudo sshfs \
        -o allow_other,default_permissions,StrictHostKeyChecking=no,password_stdin \
        "$REMOTE" "$TARGET"
}

# 验证挂载
verify_mount() {
    if [ -d "$TARGET/mapping/consume_lib/design/方法库/系统方案设计" ]; then
        log_info "挂载成功 ✓"
        log_info "访问路径: $TARGET"

        # 显示挂载内容
        echo ""
        echo "挂载内容:"
        ls "$TARGET"
        return 0
    else
        log_error "挂载验证失败 ✗"
        log_error "请检查挂载是否成功"
        return 1
    fi
}

# 卸载
do_umount() {
    log_info "正在卸载 $TARGET ..."
    sudo fusermount -u "$TARGET" 2>/dev/null || sudo umount "$TARGET" 2>/dev/null || true

    if is_mounted; then
        log_error "卸载失败"
        return 1
    else
        log_info "卸载成功 ✓"
        return 0
    fi
}

# 主逻辑
main() {
    echo "=========================================="
    echo "  Viking 远程目录挂载脚本"
    echo "=========================================="
    echo ""

    # 参数处理
    case "${1:-mount}" in
        mount)
            check_deps
            check_ssh
            umount_old
            prepare_mountpoint
            do_mount
            verify_mount
            ;;
        umount|unmount)
            if is_mounted; then
                do_umount
            else
                log_info "未检测到挂载，无需卸载"
            fi
            ;;
        status)
            if is_mounted; then
                log_info "状态: 已挂载 ✓"
                echo ""
                echo "挂载内容:"
                ls -la "$TARGET"
            else
                log_info "状态: 未挂载"
            fi
            ;;
        *)
            echo "用法: $0 {mount|umount|status}"
            echo ""
            echo "  mount   - 挂载远程目录（默认）"
            echo "  umount  - 卸载远程目录"
            echo "  status  - 查看挂载状态"
            exit 1
            ;;
    esac
}

main "$@"