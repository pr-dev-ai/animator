#!/bin/bash
# Application Control Script
# Start and stop the AI animation pipeline services

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

show_help() {
    cat << EOF
Usage: $0 [COMMAND]

Commands:
    start       Start all services (docker compose up -d)
    stop        Stop all services (docker compose down)
    restart     Restart all services
    status      Show service status
    logs        View logs (follow mode)
    help        Show this help message

Examples:
    $0 start
    $0 stop
    $0 restart
    $0 status
    $0 logs

EOF
}

check_prerequisites() {
    # Check if docker is available
    if ! command -v docker &> /dev/null; then
        echo "❌ Error: Docker is not installed or not in PATH"
        echo "   Install Docker first. See SETUP.md for instructions."
        exit 1
    fi

    # Check if docker compose is available
    if ! docker compose version &> /dev/null; then
        echo "❌ Error: Docker Compose v2 is not available"
        echo "   Install Docker Compose v2. See SETUP.md for instructions."
        exit 1
    fi

    # Check if .env exists
    if [ ! -f ".env" ]; then
        echo "⚠️  Warning: .env file not found"
        echo "   Creating from .env.example..."
        if [ -f ".env.example" ]; then
            cp .env.example .env
            echo "✅ Created .env from .env.example"
        else
            echo "❌ Error: .env.example not found"
            exit 1
        fi
    fi
}

start_services() {
    echo "🚀 Starting AI animation pipeline services..."
    echo ""
    check_prerequisites
    
    # Check GPU if available
    if command -v nvidia-smi &> /dev/null; then
        echo "Checking GPU access in Docker..."
        if ! docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi &> /dev/null; then
            echo "⚠️  Warning: GPU not accessible in Docker"
            echo "   Services will start but may not have GPU acceleration"
            echo "   Run './scripts/check_gpu_docker.sh' for diagnostics"
            echo ""
        else
            echo "✅ GPU access verified"
            echo ""
        fi
    fi

    docker compose up -d
    
    echo ""
    echo "✅ Services started!"
    echo ""
    echo "Services:"
    echo "  - ComfyUI:    http://localhost:8188"
    echo "  - Piper TTS:   http://localhost:10200"
    echo "  - Rhubarb:    (CLI container, use docker exec)"
    echo ""
    echo "Use '$0 status' to check service health"
    echo "Use '$0 logs' to view logs"
}

stop_services() {
    echo "🛑 Stopping AI animation pipeline services..."
    docker compose down
    echo ""
    echo "✅ Services stopped"
}

restart_services() {
    echo "🔄 Restarting AI animation pipeline services..."
    stop_services
    sleep 2
    start_services
}

show_status() {
    echo "📊 Service Status:"
    echo ""
    docker compose ps
    echo ""
    
    # Check if services are healthy
    if docker compose ps | grep -q "healthy"; then
        echo "✅ All services are healthy"
    elif docker compose ps | grep -q "unhealthy"; then
        echo "⚠️  Some services are unhealthy. Check logs with '$0 logs'"
    fi
}

show_logs() {
    echo "📋 Viewing logs (Ctrl+C to exit)..."
    echo ""
    docker compose logs -f --tail=200
}

# Main command handling
case "${1:-help}" in
    start)
        start_services
        ;;
    stop)
        stop_services
        ;;
    restart)
        restart_services
        ;;
    status)
        show_status
        ;;
    logs)
        show_logs
        ;;
    help|--help|-h)
        show_help
        ;;
    *)
        echo "❌ Unknown command: $1"
        echo ""
        show_help
        exit 1
        ;;
esac
