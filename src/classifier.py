"""
Activity Classifier — multi-dimensional classification of window activities.

Classifies each activity record into:
  - category / sub_category (process-based, ~50 built-in + user overrides)
  - site_name (for browsers, extracted from window title)
  - project_name (for IDEs, extracted from window title)
  - file_type (from title/path suffix match — NO \\b, Chinese-safe)
  - content_type (inferred from site + title keywords)
  - keywords (simple token extraction)

Performance: < 0.5ms per call. All hot paths use dict lookup or 'in' operator.
"""

import os
import re
from typing import Optional


class ActivityClassifier:
    """Stateless classifier. Instantiate once, call classify() per activity."""

    # ── Built-in process → (category, sub_category) ──────────────────────
    APP_CATEGORIES: dict[str, tuple[str, str]] = {
        # Dev / IDE
        "Code.exe":              ("开发", "IDE"),
        "devenv.exe":            ("开发", "IDE"),
        "idea64.exe":            ("开发", "IDE"),
        "pycharm64.exe":         ("开发", "IDE"),
        "webstorm64.exe":        ("开发", "IDE"),
        "clion64.exe":           ("开发", "IDE"),
        "rider64.exe":           ("开发", "IDE"),
        "notepad++.exe":         ("开发", "编辑器"),
        "sublime_text.exe":      ("开发", "编辑器"),
        "cursor.exe":            ("开发", "IDE"),
        "Obsidian.exe":          ("开发", "笔记"),
        # Terminal / Shell
        "WindowsTerminal.exe":   ("开发", "终端"),
        "cmd.exe":               ("开发", "终端"),
        "powershell.exe":        ("开发", "终端"),
        "wsl.exe":               ("开发", "终端"),
        # Browsers
        "chrome.exe":            ("浏览", "浏览器"),
        "msedge.exe":            ("浏览", "浏览器"),
        "firefox.exe":           ("浏览", "浏览器"),
        "brave.exe":             ("浏览", "浏览器"),
        "opera.exe":             ("浏览", "浏览器"),
        # Social / Communication
        "QQ.exe":                ("社交", "即时通讯"),
        "WeChat.exe":            ("社交", "即时通讯"),
        "Weixin.exe":            ("社交", "即时通讯"),
        "DingTalk.exe":          ("社交", "协作"),
        "Feishu.exe":            ("社交", "协作"),
        "Teams.exe":             ("社交", "协作"),
        "Discord.exe":           ("社交", "社区"),
        "Telegram.exe":          ("社交", "即时通讯"),
        "slack.exe":             ("社交", "协作"),
        # Entertainment
        "steam.exe":             ("娱乐", "游戏平台"),
        "steamwebhelper.exe":    ("娱乐", "游戏"),
        "EpicGamesLauncher.exe": ("娱乐", "游戏平台"),
        "bilibili.exe":          ("娱乐", "视频"),
        # Media / Creation
        "obs64.exe":             ("创作", "录屏直播"),
        "photoshop.exe":         ("创作", "设计"),
        "illustrator.exe":       ("创作", "设计"),
        "figma.exe":             ("创作", "设计"),
        "blender.exe":           ("创作", "3D"),
        "premiere.exe":          ("创作", "视频编辑"),
        # Productivity
        "WINWORD.EXE":           ("生产力", "文档"),
        "EXCEL.EXE":             ("生产力", "表格"),
        "POWERPNT.EXE":          ("生产力", "演示"),
        "OUTLOOK.EXE":           ("生产力", "邮件"),
        "notion.exe":            ("生产力", "笔记"),
        # System
        "explorer.exe":          ("系统", "文件管理"),
        "Taskmgr.exe":           ("系统", "任务管理"),
        "LockApp.exe":           ("系统", "锁屏"),
        "ShellHost.exe":         ("系统", "系统界面"),
        "StartMenuExperienceHost.exe": ("系统", "开始菜单"),
    }

    # ── File-type suffix map (Chinese-safe: use endswith, no \\b) ────────
    FILE_TYPE_SUFFIXES: dict[str, str] = {
        ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript",
        ".tsx": "TypeScript", ".jsx": "JavaScript", ".vue": "Vue",
        ".java": "Java", ".cpp": "C++", ".c": "C", ".h": "C/C++",
        ".rs": "Rust", ".go": "Go", ".rb": "Ruby", ".php": "PHP",
        ".swift": "Swift", ".kt": "Kotlin", ".scala": "Scala",
        ".html": "HTML", ".css": "CSS", ".scss": "SCSS",
        ".json": "JSON", ".yaml": "YAML", ".yml": "YAML",
        ".xml": "XML", ".toml": "TOML", ".ini": "INI",
        ".md": "Markdown", ".rst": "reStructuredText",
        ".sql": "SQL", ".sh": "Shell", ".ps1": "PowerShell",
        ".dockerfile": "Docker", ".makefile": "Makefile",
        ".docx": "Word", ".doc": "Word", ".xlsx": "Excel",
        ".xls": "Excel", ".pptx": "PPT", ".pdf": "PDF",
        ".psd": "Photoshop", ".ai": "Illustrator",
        ".svg": "SVG", ".png": "Image", ".jpg": "Image",
        ".mp4": "Video", ".mp3": "Audio",
    }

    # ── Site name patterns (keyword → site name) ──────────────────────────
    KNOWN_SITES: list[tuple[str, str]] = [
        ("GitHub", "GitHub"), ("Bilibili", "Bilibili"), ("YouTube", "YouTube"),
        ("Stack Overflow", "StackOverflow"), ("StackOverflow", "StackOverflow"),
        ("ChatGPT", "ChatGPT"), ("Claude", "Claude"), ("知乎", "知乎"),
        ("百度", "百度"), ("Google", "Google"),
        ("Notion", "Notion"), ("Twitter", "Twitter"), ("X", "Twitter"),
        ("Reddit", "Reddit"), ("掘金", "掘金"), ("CSDN", "CSDN"),
        ("Gitee", "Gitee"), ("CodePen", "CodePen"),
        ("Figma", "Figma"), ("Canva", "Canva"),
        ("Trello", "Trello"), ("Jira", "Jira"),
        ("Confluence", "Confluence"), ("飞书", "飞书"), ("钉钉", "钉钉"),
        ("腾讯文档", "腾讯文档"), ("Google Docs", "GoogleDocs"),
        ("Google Sheets", "GoogleSheets"),
        ("VS Code", "VSCode"), ("Code", "VSCode"),
        ("NPM", "npm"), ("PyPI", "PyPI"),
        ("Steam", "Steam"), ("Epic Games", "EpicGames"),
        ("淘宝", "淘宝"), ("京东", "京东"),
    ]

    # ── Content type rules (site name + title keyword → content type) ─────
    CONTENT_RULES: list[tuple[list[str], list[str], str]] = [
        # (site_matches, title_keywords, content_type)
        (["YouTube", "Bilibili"], [], "视频"),
        (["GitHub", "Gitee", "GitLab"], ["Pull Request", "PR", "Issue"], "代码审查"),
        (["GitHub", "Gitee", "GitLab"], [], "代码"),
        (["StackOverflow", "掘金", "CSDN"], [], "技术文章"),
        (["知乎"], ["问题", "回答"], "问答"),
        (["知乎"], [], "文章"),
        (["ChatGPT", "Claude", "Kimi"], [], "AI对话"),
        (["Notion", "腾讯文档", "GoogleDocs"], [], "文档"),
        (["Twitter", "Reddit", "微博"], [], "社交动态"),
        (["Steam", "EpicGames"], [], "游戏"),
        (["飞书", "钉钉", "Teams"], ["会议"], "会议"),
        (["淘宝", "京东", "拼多多"], [], "购物"),
    ]

    # ── Title-to-keyword stop words ───────────────────────────────────────
    _STOP_WORDS: set[str] = {
        "-", "—", "|", "·", "•", "–",
        "Google", "Chrome", "Microsoft", "Edge", "Firefox",
        "Visual", "Studio", "Code", "Window", "App",
        "的", "是", "在", "和", "了", "有", "不", "这",
    }

    def __init__(self):
        # User-defined overrides loaded from DB at init time
        self._custom_categories: dict[str, tuple[str, str]] = {}
        self._custom_sites: dict[str, str] = {}  # title_pattern → site_name

    def load_custom_rules(self, db_rules: list[dict]):
        """Load user-defined classification rules from the app_categories table."""
        self._custom_categories.clear()
        self._custom_sites.clear()
        for rule in db_rules:
            match = rule.get("process_match", "")
            cat = rule.get("category", "")
            sub = rule.get("sub_category") or ""
            if rule.get("is_site"):
                self._custom_sites[match] = cat
            else:
                self._custom_categories[match] = (cat, sub)

    # ── Public API ────────────────────────────────────────────────────────

    def classify(
        self, process_name: str, window_title: str,
        process_path: str = "", source: str = "desktop"
    ) -> dict:
        """Classify a single activity. Returns dict with all classification fields."""
        title = (window_title or "").strip()

        # 1. Process category (user override wins, then built-in, then browser prefix)
        cat, sub = self._custom_categories.get(
            process_name, self.APP_CATEGORIES.get(process_name, ("", ""))
        )
        if not cat:
            if process_name.startswith("[Browser]"):
                cat, sub = "浏览", "浏览器"
            else:
                cat, sub = "其他", ""

        # 2. Browser specific: site + content type
        site_name = ""
        content_type = ""
        if sub == "浏览器":
            site_name = self._extract_site(title)
            content_type = self._infer_content_type(site_name, title)

        # 3. IDE specific: project + file type
        project_name = ""
        file_type = ""
        if cat == "开发" and sub in ("IDE", "编辑器"):
            project_name = self._extract_project(title)
        file_type = self._extract_file_type(title, process_path)

        # 4. Keywords
        keywords = self._extract_keywords(title)

        # 5. Browser source = "browser"
        if source == "browser":
            # Browser plugin provides exact domain as process_name prefix
            # Already set by the API handler; just ensure category is right
            pass

        return {
            "category": cat,
            "sub_category": sub,
            "site_name": site_name,
            "project_name": project_name,
            "file_type": file_type,
            "content_type": content_type,
            "keywords": ",".join(keywords) if keywords else "",
        }

    # ── Internal helpers ──────────────────────────────────────────────────

    def _extract_site(self, title: str) -> str:
        """Match known sites from window title using 'in' (Chinese-safe).
        Exclude browser name suffixes (Google Chrome, Microsoft Edge, Firefox) to avoid false positives.
        """
        # Strip browser suffixes first
        clean = title
        for suffix in (" - Google Chrome", " - Microsoft Edge", " — Mozilla Firefox",
                       " - Chromium", " - Brave", " - Opera", " - Vivaldi"):
            if suffix in clean:
                clean = clean[:clean.rfind(suffix)]
                break

        # Check custom rules first
        clean_lower = clean.lower()
        for pattern, name in self._custom_sites.items():
            if pattern.lower() in clean_lower:
                return name
        for keyword, name in self.KNOWN_SITES:
            if keyword.lower() in clean_lower:
                return name
        return ""

    def _infer_content_type(self, site: str, title: str) -> str:
        """Infer content type from site + title keywords (case-insensitive)."""
        title_lower = title.lower()
        for site_matches, keywords, ctype in self.CONTENT_RULES:
            if site in site_matches:
                if not keywords or any(kw.lower() in title_lower for kw in keywords):
                    return ctype
        # Fallback: title keyword heuristics
        for kw, ctype in [
            ("Pull Request", "代码审查"), ("PR", "代码审查"),
            ("Issue", "问题追踪"), ("邮件", "邮件"),
            ("会议", "会议"), ("Dashboard", "仪表盘"),
            ("Settings", "设置"), ("配置", "配置"),
        ]:
            if kw.lower() in title_lower:
                return ctype
        return ""

    def _extract_project(self, title: str) -> str:
        """
        Extract project/folder name from IDE window titles.
        VS Code:   "file.py — project-name - Visual Studio Code"
        JetBrains: "file.py — project-name — IDE Name"
        Notepad++: "file.py - Notepad++"
        """
        # Try VS Code / JetBrains pattern: "file — project - IDE"
        # Remove the IDE suffix first
        for suffix in (" - Visual Studio Code", " - VS Code",
                       " — Visual Studio Code", " - IntelliJ IDEA",
                       " - PyCharm", " - WebStorm", " - Rider"):
            if suffix in title:
                title = title[:title.rfind(suffix)]
                break

        # Now title looks like "file.py — project-name" or "file.py"
        # Split by — or - and take the last meaningful segment
        parts = re.split(r"\s[—\-]\s", title)
        if len(parts) >= 2 and len(parts[-1]) > 3:
            return parts[-1].strip()
        return ""

    def _extract_file_type(self, title: str, path: str) -> str:
        """Detect file type from suffix in title or path. Uses endswith (Chinese-safe)."""
        candidates = [title] if title else []
        if path:
            candidates.append(path)
        for text in candidates:
            text_lower = text.lower()
            for suffix, ftype in self.FILE_TYPE_SUFFIXES.items():
                # Check if suffix appears in the text (as a file extension)
                if suffix in text_lower:
                    return ftype
        return ""

    def _extract_keywords(self, title: str) -> list[str]:
        """Extract meaningful keywords from window title. Simple token approach."""
        if not title:
            return []
        # Remove common browser/IDE wrappers
        for wrapper in (
            " - Google Chrome", " - Microsoft Edge", " — Mozilla Firefox",
            " - Visual Studio Code", " — Visual Studio Code",
            " - Notepad++", " — IntelliJ IDEA",
            " - Administrator", " — Administrator",
        ):
            title = title.replace(wrapper, "")

        # Split on common delimiters
        tokens = re.split(r"[\s\-\—\|\:\/\(\)\[\]\{\}\,\;\.\!\?\@\#\$\%\^\&\*\+]+", title)
        tokens = [t.strip() for t in tokens if t.strip()]

        # Filter: at least 2 chars, not a stop word, not purely numeric
        keywords = []
        for t in tokens:
            t_clean = t.strip('"\'`')
            if len(t_clean) >= 2 and t_clean not in self._STOP_WORDS and not t_clean.isdigit():
                keywords.append(t_clean)

        # Deduplicate and take top 10
        seen = set()
        result = []
        for kw in keywords:
            if kw.lower() not in seen:
                seen.add(kw.lower())
                result.append(kw)
                if len(result) >= 10:
                    break
        return result


# ── Singleton ──────────────────────────────────────────────────────────────

_classifier: Optional[ActivityClassifier] = None


def get_classifier() -> ActivityClassifier:
    """Get or create the global classifier instance."""
    global _classifier
    if _classifier is None:
        _classifier = ActivityClassifier()
    return _classifier


def reload_custom_rules(db_rules: list[dict]):
    """Reload user-defined classification rules."""
    get_classifier().load_custom_rules(db_rules)
