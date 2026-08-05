"""Entry point for PLC Offset Gateway (AutoBoardOffset_YOLO_2Mat)."""

import sys
import uvicorn

if __name__ == "__main__":
    print("🚀 Starting PLC Offset Gateway (AutoBoardOffset_YOLO_2Mat)...")
    print("📋 Port: 8083")
    print("📚 API Docs: http://localhost:8083/docs")
    print("🔧 Press Ctrl+C to stop")
    print()

    try:
        uvicorn.run(
            "plc_offset_gateway:app",
            host="0.0.0.0",
            port=8083,
            reload=False,
            log_level="info",
        )
    except KeyboardInterrupt:
        print("\n👋 Server stopped by user")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        sys.exit(1)
