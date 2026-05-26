import sys, traceback, os
print("Python " + str(sys.version), flush=True)
print("Loading app...", flush=True)

try:
    from app import app
    print("App loaded OK", flush=True)
except Exception as e:
    print("=== STARTUP ERROR ===", flush=True)
    print(str(e), flush=True)
    traceback.print_exc(file=sys.stdout)
    sys.stdout.flush()
    sys.exit(1)

import uvicorn
port = int(os.environ.get("PORT", 8000))
print("Starting on port " + str(port), flush=True)
uvicorn.run(app, host="0.0.0.0", port=port)
