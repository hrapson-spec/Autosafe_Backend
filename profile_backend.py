import cProfile
import os
import pstats
import signal
import sys

import uvicorn


def run_server():
    # Run uvicorn programmatically
    # We use workers=1 to keep it simple for profiling (multi-process profiling is harder)
    uvicorn.run("main:app", host="127.0.0.1", port=8000, log_level="warning", workers=1)

def main():
    print("Starting server with profiling...")
    profiler = cProfile.Profile()
    profiler.enable()
    
    try:
        run_server()
    except KeyboardInterrupt:
        pass
    finally:
        profiler.disable()
        print("\nSaving profile stats...")
        stats = pstats.Stats(profiler)
        stats.strip_dirs()
        stats.sort_stats('cumulative')
        stats.dump_stats('backend.prof')
        
        # Print top 20 lines
        print("\n--- TOP 20 CUMULATIVE TIME ---")
        stats.print_stats(20)
        
        print("\n--- TOP 20 TOTAL TIME ---")
        stats.sort_stats('tottime')
        stats.print_stats(20)

if __name__ == "__main__":
    main()
