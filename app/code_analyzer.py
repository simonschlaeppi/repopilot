import requests
import base64
from collections import defaultdict

from app.health.models import StructuredCodeData

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

# Dependency manifest / lockfile names recognized by is_lockfile().
LOCKFILE_NAMES = {
    "requirements.txt",
    "package.json",
    "package-lock.json",
    "pyproject.toml",
    "setup.py",
    "setup.cfg",
    "pipfile",
    "pipfile.lock",
    "poetry.lock",
    "go.mod",
    "go.sum",
    "cargo.toml",
    "cargo.lock",
    "gemfile",
    "gemfile.lock",
    "yarn.lock",
    "pnpm-lock.yaml",
    "composer.json",
    "composer.lock",
    "pom.xml",
    "build.gradle",
    "mix.exs",
}

def is_lockfile(path: str) -> bool:
    """Check if a file path is a dependency manifest / lockfile.

    Recognizes common manifests such as requirements.txt, package.json,
    pyproject.toml, setup.py, go.mod, Cargo.toml, and similar files. Matching
    is done on the file's basename (case-insensitive) so nested paths like
    ``backend/requirements.txt`` are detected.
    """
    basename = path.replace("\\", "/").split("/")[-1].lower()
    return basename in LOCKFILE_NAMES

def is_important_file(filename: str) -> bool:
    """Check if a file is worth analyzing.

    Scoped to AI content-sample selection ONLY: it decides which files'
    *contents* are worth fetching for the AI code summary. It deliberately
    excludes Markdown and other non-source files. It MUST NOT be used to decide
    which paths the Signal_Extractor can classify — that is governed by the
    classification predicates below and retained in ``StructuredCodeData.files``
    independently (Req 1.8/1.9).
    """
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


# --------------------------------------------------------------------------- #
# Classification predicates (Req 1.3, 1.8; Design guarantee (0)/(1)).
#
# These identify classification-relevant paths so the tree scan can retain them
# in ``StructuredCodeData.files`` regardless of ``is_important_file`` (which is
# scoped only to AI content-sample selection) and regardless of the
# content-fetch budget. They mirror the classification logic in
# ``app.health.signals`` so the Signal_Extractor sees the same paths; matching is
# done on the normalized (forward-slash, lowercase) path so nested entries such
# as ``docs/guide.md`` are detected.
# --------------------------------------------------------------------------- #

def _normalize_path(path: str) -> str:
    """Return the path with backslashes converted to forward slashes."""
    return path.replace("\\", "/")


def _path_basename(path: str) -> str:
    """Return the lowercase final path component."""
    return _normalize_path(path).rsplit("/", 1)[-1].strip().lower()


def _path_segments(path: str) -> list[str]:
    """Return the lowercase '/'-separated segments of a path."""
    return [seg for seg in _normalize_path(path).lower().split("/") if seg]


def is_documentation_file(path: str) -> bool:
    """A documentation file: a Markdown/reStructuredText file or a docs dir entry."""
    name = _path_basename(path)
    segments = _path_segments(path)
    if name.endswith((".md", ".markdown", ".rst")):
        return True
    # Entries under a documentation directory (docs/, doc/).
    if any(seg in ("docs", "doc") for seg in segments[:-1]):
        return True
    return False


def is_test_file(path: str) -> bool:
    """A test file: test_* / *_test names, spec files, or a tests/ dir entry."""
    name = _path_basename(path)
    segments = _path_segments(path)
    # A test/tests/spec/__tests__ directory anywhere in the path.
    if any(seg in ("test", "tests", "spec", "specs", "__tests__") for seg in segments[:-1]):
        return True
    stem = name.rsplit(".", 1)[0] if "." in name else name
    if stem.startswith("test_") or stem.startswith("test-"):
        return True
    if stem.endswith("_test") or stem.endswith("-test"):
        return True
    # JS/TS style: foo.test.js, foo.spec.ts
    if ".test." in name or ".spec." in name:
        return True
    if stem.endswith(".test") or stem.endswith(".spec"):
        return True
    return False


def is_ci_config_file(path: str) -> bool:
    """A CI configuration artifact detectable from the file tree (Req 1.3)."""
    lower = _normalize_path(path).lower()
    name = _path_basename(path)
    segments = _path_segments(path)
    # GitHub Actions workflows: .github/workflows/*.yml
    if ".github/workflows/" in lower:
        return True
    # CircleCI: .circleci/ directory
    if any(seg == ".circleci" for seg in segments):
        return True
    # Well-known single-file CI configs.
    if name in (
        ".gitlab-ci.yml",
        ".travis.yml",
        "azure-pipelines.yml",
        ".appveyor.yml",
        "appveyor.yml",
        "jenkinsfile",
        ".drone.yml",
        "bitbucket-pipelines.yml",
    ):
        return True
    return False


def is_classification_relevant(path: str) -> bool:
    """Whether a path must be retained in ``files`` for signal classification.

    A path is classification-relevant when it matches the documentation, test,
    CI_Config, or Lockfile predicate. Such paths are retained in
    ``StructuredCodeData.files`` regardless of ``is_important_file`` and
    regardless of the content-fetch budget (Req 1.8; Design guarantee (0)/(1)).
    Lockfiles are handled separately by the scan's dedicated retention path.
    """
    return (
        is_documentation_file(path)
        or is_test_file(path)
        or is_ci_config_file(path)
    )

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
        lockfiles = []
        classification_files = []
        non_lockfile_capped = False

        for item in data.get("tree", []):
            if item["type"] != "blob":
                continue

            path = item["path"]

            # Detected lockfiles are ALWAYS retained, even beyond the cap.
            if is_lockfile(path):
                if path not in lockfiles:
                    lockfiles.append(path)
                continue

            # Classification-relevant paths (documentation / test / CI_Config)
            # are ALWAYS retained for signal classification, regardless of
            # ``is_important_file`` (which excludes Markdown) and regardless of
            # the content-fetch budget (Req 1.8/1.9; Design guarantee (0)/(1)).
            if is_classification_relevant(path):
                if path not in classification_files:
                    classification_files.append(path)
                continue

            # The max_files cap governs only non-lockfile, non-classification
            # files (the AI content-sample selection).
            if non_lockfile_capped:
                continue

            if is_important_file(path):
                files.append(path)
                if len(files) >= max_files:
                    non_lockfile_capped = True

        # Append every classification-relevant path unconditionally so the early
        # cap on AI-sample files can never drop them (Req 1.8/1.9).
        for classification_file in classification_files:
            if classification_file not in files:
                files.append(classification_file)

        # Append every detected lockfile unconditionally so the early cap on
        # non-lockfile files can never drop them.
        for lockfile in lockfiles:
            if lockfile not in files:
                files.append(lockfile)

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

def analyze_repository(owner: str, repo: str, max_files: int = 20) -> StructuredCodeData:
    """Scan the repo tree and fetch contents, returning STRUCTURED data.

    - ``max_files`` is the Content_Fetch_Budget: it bounds ONLY how many
      non-lockfile, non-classification file paths are admitted as AI content
      *samples* (whose contents feed the AI summary). It does NOT limit which
      paths appear in ``files`` for signal classification (Req 1.9).
    - ``files`` is the Signal_Classification_Set: it reflects the full
      repository tree's classification-relevant paths — EVERY documentation /
      test / CI_Config path and EVERY detected lockfile — plus a
      budget-bounded set of AI-sample files. Classification-relevant paths and
      lockfiles are retained regardless of ``max_files`` (Req 1.8; Design
      guarantee (0)/(1)). Non-important, non-classification files beyond the
      AI-sample budget are the only paths the cap drops.
    - ``file_contents`` includes the first-5-key-file samples AND the content of
      EVERY detected lockfile (a separate, additional fetch).

    The scan uses the existing single tree listing plus the existing bounded
    content fetches — it introduces no new external analysis calls (Req 7.4).

    Reuses the existing GitHub fetch flow (``get_repository_tree`` /
    ``get_file_content``); performs no registry lookups or other external data
    source access. A genuine fetch failure yields absence in ``file_contents``
    (the rare "present but contents unavailable" fallback) rather than an empty
    entry.
    """
    files = get_repository_tree(owner, repo, max_files=max_files)

    languages: dict[str, int] = defaultdict(int)
    key_files: list[str] = []

    for file_path in files:
        languages[get_file_language(file_path)] += 1

        # Prioritize certain important files (same heuristic as
        # analyze_code_structure).
        if any(name in file_path.lower() for name in
               ["main", "app", "server", "index", "config", "setup", "init"]):
            key_files.append(file_path)

    file_contents: dict[str, str] = {}

    # (a) Existing sample behavior: first 5 key files' content.
    for file_path in key_files[:5]:
        content = get_file_content(owner, repo, file_path)
        if content:
            file_contents[file_path] = content

    # (b) Guaranteed lockfile fetch: EVERY detected lockfile's content via a
    # separate/additional fetch. Only store successfully fetched non-empty
    # content; a failed/empty fetch leaves the path absent (rare fallback).
    for file_path in files:
        if not is_lockfile(file_path):
            continue
        if file_path in file_contents:
            continue
        content = get_file_content(owner, repo, file_path)
        if content:
            file_contents[file_path] = content

    return StructuredCodeData(
        files=tuple(files),
        languages=dict(languages),
        key_files=tuple(key_files),
        file_count=len(files),
        file_contents=file_contents,
    )


def _format_code_summary(data: StructuredCodeData) -> str:
    """Format StructuredCodeData into the free-text summary.

    Reproduces byte-for-byte the summary that ``analyze_code_structure``
    historically returned so ``explain_repo_with_code`` is unaffected. Only the
    first-5 key files are shown as samples (500-char previews, in key-file
    order); the extra lockfile contents retained in ``file_contents`` are NOT
    included here.
    """
    if not data.files:
        return "No source files found."

    # Rebuild the key-file samples exactly as analyze_code_structure did: iterate
    # the first 5 key files in order and include only those whose content was
    # successfully fetched (present in file_contents), re-sliced to 500 chars.
    code_samples = []
    for file_path in data.key_files[:5]:
        content = data.file_contents.get(file_path)
        if content:
            code_samples.append({
                "path": file_path,
                "language": get_file_language(file_path),
                "preview": content[:500],
            })

    summary = f"""
Repository Structure Analysis:
- Total files analyzed: {data.file_count}
- Languages detected: {dict(data.languages)}
- Key files: {', '.join(data.key_files[:5])}

File List:
{chr(10).join(data.files[:20])}

Code Samples (first 500 chars of key files):
"""

    for sample in code_samples:
        summary += f"\n\n--- {sample['path']} ({sample['language']}) ---\n{sample['preview']}"

    return summary


def get_code_summary(owner: str, repo: str) -> str:
    """Get a comprehensive code summary for a repository.

    Thin adapter over ``analyze_repository``: derives the structured data once
    and formats it into the same free-text summary the AI path consumes. Output
    is byte-for-byte identical to the previous ``analyze_code_structure`` result.
    """
    data = analyze_repository(owner, repo, max_files=20)
    return _format_code_summary(data)
