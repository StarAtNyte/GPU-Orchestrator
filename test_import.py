import sys
import os
sys.path.append(os.path.abspath('worker'))
try:
    import shared
    print(f"shared file: {shared.__file__}")
    from shared import worker_pb2
    print("Success")
except Exception as e:
    print(f"Error: {e}")
