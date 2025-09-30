import zipfile
import os
import shutil
import time

# Configuration
source_folder = "G:/Projects/lunara-sentinel"
clone_folder = "G:/Projects/temp-lunara-clone"
output_folder = "G:/Projects/flash-lunara"
output_zip = os.path.join(output_folder, "LunaraSentinelBundle.zip")

# Optional injected files
image_file = os.path.join(source_folder, "assets", "logo.png")
bat_file = os.path.join(source_folder, "launch_sentinel.bat")
docker_yml = os.path.join(source_folder, "docker-compose.yml")

# Step 1: Prepare output folder
os.makedirs(output_folder, exist_ok=True)

# Step 2: Clone the source safely
if os.path.exists(clone_folder):
    shutil.rmtree(clone_folder)
shutil.copytree(source_folder, clone_folder)
print(f"📁 Cloned project to: {clone_folder}")

# Step 3: Inject updated files (optional overrides)
if os.path.exists(docker_yml):
    shutil.copy(docker_yml, os.path.join(clone_folder, "docker-compose.yml"))
    print("🐳 Docker Compose injected")

if os.path.exists(bat_file):
    shutil.copy(bat_file, os.path.join(clone_folder, "launch_sentinel.bat"))
    print("🧪 BAT launcher injected")

if os.path.exists(image_file):
    shutil.copy(image_file, os.path.join(clone_folder, "logo.png"))
    print("🖼️ Logo injected")

# Step 4: Create the zip bundle
with zipfile.ZipFile(output_zip, 'w', zipfile.ZIP_DEFLATED) as zipf:
    for root, dirs, files in os.walk(clone_folder):
        for file in files:
            filepath = os.path.join(root, file)
            arcname = os.path.relpath(filepath, clone_folder)

            try:
                zipf.write(filepath, arcname)
                print(f"✅ Added: {arcname}")
            except Exception as e:
                print(f"⚠️ Failed to add {arcname}: {e}")

print(f"\n📦 Bundle sealed successfully at: {output_zip}")