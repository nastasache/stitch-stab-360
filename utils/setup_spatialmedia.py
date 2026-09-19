import sys, os
sys.dont_write_bytecode = True
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
"""Automated download and installation helper for Google Spatial Media metadata tool.

Downloads the spatial-media repository archive from GitHub, extracts the Python
package into `scripts/spatialmedia`, and cleans up temporary extraction artifacts.
"""

import urllib.request
import zipfile
import io
import shutil

def main():
    """Download Google Spatial Media archive and install to scripts/spatialmedia."""
    url = "https://github.com/google/spatial-media/archive/refs/heads/master.zip"
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) if os.path.basename(os.path.dirname(os.path.abspath(__file__))) == "scripts" else os.path.dirname(os.path.abspath(__file__))
    final_dir = os.path.join(repo_root, "scripts", "spatialmedia")

    print("Downloading spatial-media from Github...")
    try:
        req = urllib.request.Request(
            url, 
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        )
        with urllib.request.urlopen(req) as response:
            zip_data = response.read()
    except Exception as e:
        print(f"Error downloading spatial-media: {e}")
        return

    print("Extracting files...")
    try:
        with zipfile.ZipFile(io.BytesIO(zip_data)) as zip_ref:
            zip_ref.extractall(extract_dir)
    except Exception as e:
        print(f"Error extracting zip: {e}")
        return

    # Find the folder name inside extraction (usually spatial-media-master)
    contents = os.listdir(extract_dir)
    if not contents:
        print("Extraction directory is empty.")
        return
    
    root_folder = os.path.join(extract_dir, contents[0])
    src_spatialmedia = os.path.join(root_folder, "spatialmedia")
    
    if not os.path.exists(src_spatialmedia):
        print(f"Could not find spatialmedia directory inside {root_folder}")
        return

    print(f"Copying {src_spatialmedia} to {final_dir}...")
    shutil.copytree(src_spatialmedia, final_dir, dirs_exist_ok=True)
    print("Setup completed successfully!")

if __name__ == "__main__":
    main()
