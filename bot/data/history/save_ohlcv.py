import sys
import json
import pandas as pd
from pathlib import Path

def convert(input_path, output_path):
    with open(input_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    if not data.get('success'):
        print(f"FAILED: {output_path} - API returned success=false")
        return False
    bars = data.get('bars', [])
    if not bars:
        print(f"FAILED: {output_path} - no bars")
        return False
    df = pd.DataFrame(bars)
    df = df.rename(columns={'time': 'timestamp'})
    df = df[['timestamp', 'open', 'high', 'low', 'close', 'volume']]
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(f"SAVED: {output_path} ({len(df)} bars)")
    return True

if __name__ == '__main__':
    convert(sys.argv[1], sys.argv[2])
