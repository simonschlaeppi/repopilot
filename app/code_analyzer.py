import requests
import base64
from collections import defaultdict

# Languages and their common extensions
LANGUAGE_EXTENSIONS = {
    "python": [".py"],
    "javascript": [".js", ".jsx"],
    "typescript": [".ts", ".tsx"],
    "java": [".java"],
    "csharp": [".cs"],
    "ruby": [".rb"],
    "go": [".go"],
    "rust": [".rs"],
    "cpp": [".cpp", ".cc", ".h", ".hpp"],
    "c": [".c", ".h"],
    "html": [".html", ".htm"],
    "css": [".css", ".scss", ".sass", ".less"],
    "sql": [".sql"],
    "json": [".json"],
    "yaml": [".yaml", ".yml"],
    "markdown": [".md", ".markdown"],
}

def get_file_extension(filename: str) -> str:
    """Extract file extension from filename."""
    if "." not in filename:
        return ""
    return "." + filename.split(".")[-1]

def get_file_language(filename: str) -> str:
    """Determine the programming language from file extension."""
    ext = get_file_extension(filename).lower()
    for language, extensions in LANGUAGE_EXTENSIONS.items():
        if ext in extensions:
            return language
    return "other"

def is_important_file(filename: str) -> bool:
    """Check if a file is worth analyzing."""
    important_names = {
        "package.json", "requirements.txt", "pyproject.toml", "setup.py",
        "Makefile", "Dockerfile", "docker-compose.yml", ".github", "main.py",
        "app.py", "index.js", "main.js", "index.ts", "server.py", "server.js",
        "config.py", "config.js", "settings.py", "gradle.build", "pom.xml",
        "go.mod", "Cargo.toml", "mix.exs"
    }
    
    filename_lower = filename.lower()
    if filename in important_names:
        return True
    
    language = get_file_language(filename)
    return language not in ["other", "markdown"]

def get_repository_default_branch(owner: str, repo: str) -> str:
    """Fetch the repository default branch from GitHub."""
    url = f"https://api.github.com/repos/{owner}/{repo}"
    response = requests.get(url)
    response.raise_for_status()
    data = response.json()
    return data.get("default_branch", "main")


def get_repository_tree(owner: str, repo: str, max_files: int = 100) -> list:
    """Fetch the repository file tree from GitHub."""
    try:
        branch = get_repository_default_branch(owner, repo)
        url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{branch}?recursive=1"

        response = requests.get(url)
        response.raise_for_status()

        data = response.json()
        files = []

        for item in data.get("tree", []):
            if item["type"] == "blob" and is_important_file(item["path"]):
                files.append(item["path"])
                if len(files) >= max_files:
                    break

        return files
    except Exception as e:
        print(f"Error fetching repository tree: {e}")
        return []

def get_file_content(owner: str, repo: str, file_path: str) -> str:
    """Fetch the content of a single file from GitHub."""
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/{file_path}"
    
    try:
        response = requests.get(url)
        response.raise_for_status()
        
        data = response.json()
        if "content" in data:
            content = base64.b64decode(data["content"]).decode("utf-8")
            return content
        return ""
    except Exception as e:
        print(f"Error fetching {file_path}: {e}")
        return ""

def analyze_code_structure(owner: str, repo: str, max_files: int = 20) -> str:
    """Analyze repository code structure and return a summary."""
    files = get_repository_tree(owner, repo, max_files=max_files)
    
    if not files:
        return "No source files found."
    
    code_structure = {
        "files": [],
        "languages": defaultdict(int),
        "key_files": [],
        "file_count": len(files),
    }
    
    # Categorize files by language
    for file_path in files:
        language = get_file_language(file_path)
        code_structure["languages"][language] += 1
        code_structure["files"].append(file_path)
        
        # Prioritize certain important files
        if any(name in file_path.lower() for name in 
               ["main", "app", "server", "index", "config", "setup", "init"]):
            code_structure["key_files"].append(file_path)
    
    # Fetch content of key files for deeper analysis
    code_samples = []
    for file_path in code_structure["key_files"][:5]:  # Limit to 5 files
        content = get_file_content(owner, repo, file_path)
        if content:
            # Limit content to 500 chars per file to avoid token explosion
            code_samples.append({
                "path": file_path,
                "language": get_file_language(file_path),
                "preview": content[:500]
            })
    
    # Format summary for AI analysis
    summary = f"""
Repository Structure Analysis:
- Total files analyzed: {code_structure['file_count']}
- Languages detected: {dict(code_structure['languages'])}
- Key files: {', '.join(code_structure['key_files'][:5])}

File List:
{chr(10).join(code_structure['files'][:20])}

Code Samples (first 500 chars of key files):
"""
    
    for sample in code_samples:
        summary += f"\n\n--- {sample['path']} ({sample['language']}) ---\n{sample['preview']}"
    
    return summary

def get_code_summary(owner: str, repo: str) -> str:
    """Get a comprehensive code summary for a repository."""
    return analyze_code_structure(owner, repo, max_files=20)
