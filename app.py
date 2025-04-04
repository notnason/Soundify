import os
import json
import uuid
from pathlib import Path
from flask import Flask, request, jsonify, render_template, send_from_directory, abort
from werkzeug.utils import secure_filename
from werkzeug.datastructures import FileStorage 

try:
    from mutagen.mp3 import MP3
    from mutagen.id3 import ID3, APIC
    from mutagen.easyid3 import EasyID3
    from mutagen.id3 import ID3NoHeaderError
    MUTAGEN_AVAILABLE = True
except ImportError:
    MUTAGEN_AVAILABLE = False
    print("*"*60)
    print("WARNING: 'mutagen' library not found. Cannot read MP3 metadata or covers.")
    print("Install it using: pip install mutagen")
    print("Metadata reading will be limited.")
    print("*"*60)


UPLOAD_FOLDER = 'uploads'
COVERS_FOLDER_NAME = 'covers'
COVERS_FOLDER = os.path.join(UPLOAD_FOLDER, COVERS_FOLDER_NAME)
LIBRARY_FILE = 'library.json'
ALLOWED_EXTENSIONS = {'mp3'}
ALLOWED_COVER_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

app = Flask(__name__, template_folder='templates')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['COVERS_FOLDER'] = COVERS_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 30 * 1024 * 1024

os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['COVERS_FOLDER'], exist_ok=True)


def allowed_file(filename):
    """Checks if the MP3 file extension is allowed."""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def allowed_cover_file(filename):
    """Checks if the cover file extension is allowed."""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_COVER_EXTENSIONS


def load_library(library_path=LIBRARY_FILE):
    """Loads the music library metadata from the JSON file."""
    if os.path.exists(library_path):
        try:
            with open(library_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if "songs" not in data or not isinstance(data["songs"], dict):
                     data["songs"] = {}
                needs_save = False
                updated_songs = {}
                for key, song_data in data.get("songs", {}).items():
                    song_id = song_data.get('id', key) 
                    if not isinstance(song_id, str) or len(song_id) < 10: 
                        song_id = str(uuid.uuid4()) 
                        needs_save = True
                        print(f"Assigning new ID {song_id} to potentially problematic entry key '{key}'")

                    if 'id' not in song_data or song_data['id'] != song_id:
                        song_data['id'] = song_id
                        needs_save = True

                    if 'audioSrc' not in song_data and 'filename' in song_data:
                        song_data['audioSrc'] = f'/uploads/{song_data["filename"]}'
                        needs_save = True

                    if 'coverSrc' in song_data and song_data['coverSrc'] and not song_data['coverSrc'].startswith('/'):
                         song_data['coverSrc'] = f'/{COVERS_FOLDER_NAME}/{Path(song_data["coverSrc"]).name}'
                         needs_save = True

                    updated_songs[song_id] = song_data 

                data["songs"] = updated_songs
                if needs_save:
                    print("Updating library file with standardized IDs/paths...")
                    save_library(data, library_path) 
                return data
        except (json.JSONDecodeError, IOError) as e:
            print(f"Error loading library file '{library_path}': {e}. Initializing empty library.")
            return {"songs": {}}
    else:
        print(f"Library file '{library_path}' not found. Initializing empty library.")
        return {"songs": {}}


def save_library(library_data, library_path=LIBRARY_FILE):
    """Saves the music library metadata to the JSON file."""
    try:
        if "songs" not in library_data or not isinstance(library_data["songs"], dict):
             print(f"Warning: Correcting library data structure before saving.")
             library_data = {"songs": library_data.get("songs", {})}

        corrected_songs = {}
        for key, song_data in library_data.get("songs", {}).items():
             if isinstance(song_data, dict) and 'id' in song_data:
                 corrected_songs[song_data['id']] = song_data
             else:
                 print(f"Warning: Skipping invalid song entry during save: Key '{key}', Data: {song_data}")
        library_data["songs"] = corrected_songs

        with open(library_path, 'w', encoding='utf-8') as f:
            json.dump(library_data, f, indent=4, ensure_ascii=False)
        print(f"Library saved to '{library_path}'")
        return True
    except IOError as e:
        print(f"Error saving library file '{library_path}': {e}")
        return False
    except TypeError as e:
        print(f"Error serializing library data: {e}")
        print("Problematic data structure:", library_data)
        return False


def get_song_metadata(mp3_filepath_str, song_id, manual_artist=None, uploaded_cover_file: FileStorage = None):
    """
    Attempts to read metadata, prioritize manual inputs, and handle uploaded cover.

    Args:
        mp3_filepath_str: Filename of the saved MP3 in the UPLOAD_FOLDER.
        song_id: The unique ID for the song.
        manual_artist: Artist name provided by the user (optional).
        uploaded_cover_file: FileStorage object for the uploaded cover (optional).
    """
    server_mp3_path = Path(app.config['UPLOAD_FOLDER']) / mp3_filepath_str
    metadata = {
        'id': song_id,
        'filename': mp3_filepath_str,
        'title': Path(mp3_filepath_str).stem, 
        'artist': 'Unknown Artist',
        'album': 'Unknown Album',
        'genre': 'Unknown Genre',
        'audioSrc': f'/uploads/{mp3_filepath_str}',
        'coverSrc': None
    }

    cover_saved = False
    if uploaded_cover_file and uploaded_cover_file.filename != '' and allowed_cover_file(uploaded_cover_file.filename):
        try:
            cover_ext = uploaded_cover_file.filename.rsplit('.', 1)[1].lower()
            cover_filename = f"{song_id}.{cover_ext}"
            cover_save_path = Path(app.config['COVERS_FOLDER']) / cover_filename
            uploaded_cover_file.save(cover_save_path)
            metadata['coverSrc'] = f'/{COVERS_FOLDER_NAME}/{cover_filename}'
            print(f"Saved uploaded cover art for {song_id} to {cover_save_path}")
            cover_saved = True
        except Exception as e:
            print(f"Error saving uploaded cover file for {song_id}: {e}")

    if MUTAGEN_AVAILABLE and server_mp3_path.is_file():
        try:
            audio_easy = EasyID3(server_mp3_path)
            metadata['title'] = audio_easy.get('title', [metadata['title']])[0]
            metadata['artist'] = manual_artist if manual_artist else audio_easy.get('artist', [metadata['artist']])[0]
            metadata['album'] = audio_easy.get('album', [metadata['album']])[0]
            metadata['genre'] = audio_easy.get('genre', [metadata['genre']])[0]
        except ID3NoHeaderError:
            print(f"Warning: No standard ID3 tags found for '{mp3_filepath_str}'.")
            if manual_artist:
                metadata['artist'] = manual_artist
        except Exception as e:
            print(f"Error reading basic metadata for '{mp3_filepath_str}': {e}")
            if manual_artist:
                metadata['artist'] = manual_artist

        if not cover_saved:
            try:
                audio_full = MP3(server_mp3_path, ID3=ID3)
                if audio_full.tags:
                    for tag_name in audio_full.tags:
                        if tag_name.startswith('APIC'):
                            apic_tag = audio_full.tags[tag_name]
                            mime_type = apic_tag.mime
                            image_data = apic_tag.data
                            ext = mime_type.split('/')[-1].lower() 
                            if ext == 'jpeg': ext = 'jpg'

                            
                            if ext in ALLOWED_COVER_EXTENSIONS:
                                cover_filename = f"{song_id}.{ext}"
                                cover_save_path = Path(app.config['COVERS_FOLDER']) / cover_filename
                                try:
                                    with open(cover_save_path, 'wb') as img_file:
                                        img_file.write(image_data)
                                    metadata['coverSrc'] = f'/{COVERS_FOLDER_NAME}/{cover_filename}'
                                    print(f"Saved EMBEDDED cover art for {song_id} to {cover_save_path}")
                                    cover_saved = True 
                                    break 
                                except IOError as e:
                                    print(f"Error saving embedded cover art for {song_id}: {e}")
                                break 
                            else:
                                print(f"Skipping embedded cover with unsupported mime type: {mime_type}")
            except Exception as e:
                print(f"Error reading full MP3 tags/embedded cover for '{mp3_filepath_str}': {e}")

    elif not MUTAGEN_AVAILABLE:
        metadata['artist'] = manual_artist if manual_artist else 'N/A (mutagen not installed)'
    else: 
         metadata['artist'] = manual_artist if manual_artist else 'N/A (File missing?)'

    if not metadata['artist'] or metadata['artist'] == 'Unknown Artist':
         metadata['artist'] = manual_artist if manual_artist else 'Unknown Artist' 


    return metadata

@app.route('/')
def index():
    """Serves the main HTML page."""
    return render_template('index.html')
@app.route('/upload', methods=['POST'])
def upload_file():
    """Handles MP3 file uploads along with optional artist name and cover image."""
    if 'file' not in request.files:
        return jsonify({"error": "No MP3 file part"}), 400
    mp3_file = request.files['file']

    manual_artist_name = request.form.get('artistName', "").strip() 
    cover_file = request.files.get('coverFile', None)
    if mp3_file.filename == '':
        return jsonify({"error": "No selected MP3 file"}), 400

    if not allowed_file(mp3_file.filename): 
        return jsonify({"error": f"MP3 file type not allowed for '{mp3_file.filename}'"}), 400

    if cover_file and cover_file.filename != '' and not allowed_cover_file(cover_file.filename):
         return jsonify({"error": f"Cover file type not allowed for '{cover_file.filename}'. Use {', '.join(ALLOWED_COVER_EXTENSIONS)}."}), 400

    filename = secure_filename(mp3_file.filename)
    base, ext = os.path.splitext(filename)
    counter = 1
    save_path = Path(app.config['UPLOAD_FOLDER']) / filename
    while save_path.exists():
        filename = f"{base}_{counter}{ext}"
        save_path = Path(app.config['UPLOAD_FOLDER']) / filename
        counter += 1

    metadata = None 
    try:
        mp3_file.save(save_path)
        print(f"MP3 file saved to: {save_path}")
        song_id = str(uuid.uuid4())

        metadata = get_song_metadata(filename, song_id, manual_artist_name or None, cover_file)

        library_data = load_library()
        library_data["songs"][song_id] = metadata 
        if save_library(library_data):
            return jsonify({"success": True, "message": f"File '{filename}' uploaded.", "song": metadata}), 201
        else:
             raise IOError("Failed to save library data after processing.")

    except Exception as e:
        print(f"Error during file upload processing: {e}")
        if 'save_path' in locals() and save_path.exists():
            try:
                os.remove(save_path)
                print(f"Cleaned up MP3 file: {save_path}")
            except OSError as remove_error:
                print(f"Error removing MP3 during cleanup: {remove_error}")

        if metadata and metadata.get('coverSrc'):
            cover_path_rel = metadata['coverSrc'].lstrip('/')
            if cover_path_rel.startswith(COVERS_FOLDER_NAME + '/'):
                 cover_path_abs = Path(app.config['UPLOAD_FOLDER']) / cover_path_rel 
                 if cover_path_abs.exists() and cover_path_abs.is_file():
                     try:
                         os.remove(cover_path_abs)
                         print(f"Cleaned up cover file: {cover_path_abs}")
                     except OSError as remove_cover_error:
                         print(f"Error removing cover during cleanup: {remove_cover_error}")

        return jsonify({"error": f"Failed to process file: {e}"}), 500


@app.route('/api/songs', methods=['GET'])
def get_songs():
    """Returns the list of songs from the library."""
    library_data = load_library()
    song_list = list(library_data.get("songs", {}).values())
    return jsonify(song_list)


@app.route('/api/songs/<song_id>', methods=['DELETE'])
def delete_song(song_id):
    """Deletes a song and its cover from the library and the filesystem."""
    library_data = load_library()
    if song_id not in library_data.get("songs", {}):
        print(f"Attempted to delete non-existent song ID: {song_id}")
        return jsonify({"error": "Song not found"}), 404

    song_info = library_data["songs"][song_id]
    filename = song_info.get("filename")
    cover_src = song_info.get("coverSrc") 

    audio_file_deleted = False
    cover_file_deleted = False

  
    if filename:
        audio_path = Path(app.config['UPLOAD_FOLDER']) / filename
        if audio_path.exists() and audio_path.is_file():
            if str(audio_path.resolve()).startswith(str(Path(app.config['UPLOAD_FOLDER']).resolve())):
                try:
                    os.remove(audio_path)
                    print(f"Deleted audio file: {audio_path}")
                    audio_file_deleted = True
                except OSError as e:
                    print(f"Error deleting audio file {audio_path}: {e}")
            else:
                 print(f"Security risk: Audio path {audio_path} seems outside upload folder.")
        else:
            print(f"Audio file not found for deletion, assuming deleted or issue: {audio_path}")
            audio_file_deleted = True 
    else:
         print(f"No filename associated with song ID {song_id}, cannot delete audio file.")
         audio_file_deleted = True


    if cover_src:
         cover_path_rel = cover_src.lstrip('/') 
         if cover_path_rel.startswith(COVERS_FOLDER_NAME + '/'):
             cover_path_abs = Path(app.config['UPLOAD_FOLDER']) / cover_path_rel
             if cover_path_abs.exists() and cover_path_abs.is_file():
                 if str(cover_path_abs.resolve()).startswith(str(Path(app.config['COVERS_FOLDER']).resolve())):
                     try:
                         os.remove(cover_path_abs)
                         print(f"Deleted cover file: {cover_path_abs}")
                         cover_file_deleted = True
                     except OSError as e:
                         print(f"Error deleting cover file {cover_path_abs}: {e}")
                 else:
                      print(f"Security risk: Cover path {cover_path_abs} seems outside covers folder.")
             else:
                 print(f"Cover file not found for deletion, assuming deleted or issue: {cover_path_abs}")
                 cover_file_deleted = True 
         else:
             print(f"Invalid coverSrc format or path: {cover_src}")
             cover_file_deleted = True 
    else:
         print(f"No coverSrc associated with song ID {song_id}, cannot delete cover file.")
         cover_file_deleted = True 


    if audio_file_deleted and cover_file_deleted:
        del library_data["songs"][song_id]
        if save_library(library_data):
            return jsonify({"success": True, "message": f"Song '{song_info.get('title', song_id)}' deleted."}), 200
        else:
            print(f"CRITICAL ERROR: Failed to save library after successfully deleting files for song ID {song_id}.")
            return jsonify({"error": "Files deleted, but failed to update library metadata. Please check manually."}), 500
    else:
         error_msg = "Failed to delete associated files. Library not modified."
         if not audio_file_deleted and not cover_file_deleted:
             error_msg = "Failed to delete both audio and cover files."
         elif not audio_file_deleted:
             error_msg = "Failed to delete audio file."
         elif not cover_file_deleted:
             error_msg = "Failed to delete cover file."
         print(f"Deletion failed for song {song_id}: {error_msg}")
         return jsonify({"error": error_msg}), 500


@app.route('/uploads/<path:filename>')
def serve_uploaded_file(filename):
    """Serves the uploaded audio files for playback safely."""
    upload_dir = os.path.abspath(app.config['UPLOAD_FOLDER'])
    try:
        return send_from_directory(upload_dir, filename, as_attachment=False)
    except FileNotFoundError:
         print(f"Audio file not found request: {filename}")
         abort(404, description="Audio file not found")
    except Exception as e:
        print(f"Error serving audio file {filename}: {e}")
        abort(500, description="Server error serving audio file")


@app.route('/covers/<path:filename>')
def serve_cover_file(filename):
    """Serves the extracted cover art files safely."""
    covers_dir = os.path.abspath(app.config['COVERS_FOLDER'])
    try:
        return send_from_directory(covers_dir, filename, as_attachment=False)
    except FileNotFoundError:
        print(f"Cover file not found request: {filename}")
        abort(404, description="Cover file not found")
    except Exception as e:
        print(f"Error serving cover file {filename}: {e}")
        abort(500, description="Server error serving cover file")


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)