import os
import ast


def should_skip(name):
    return name.startswith('.') or name.startswith('_')


def _relpath(path, root):
    try:
        return os.path.relpath(path, root)
    except Exception:
        return path


def parse_python_file(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            source = f.read()
    except Exception:
        return {'functions': [], 'classes': []}
    try:
        tree = ast.parse(source, filename=file_path)
    except Exception:
        return {'functions': [], 'classes': []}

    functions = []
    classes = []

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append((node.name, node.lineno))
        elif isinstance(node, ast.ClassDef):
            methods = []
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.append((item.name, item.lineno))
            classes.append((node.name, node.lineno, methods))

    functions.sort(key=lambda x: (x[0].lower(), x[1]))
    classes.sort(key=lambda x: (x[0].lower(), x[1]))
    classes = [
        (cname, clineno, sorted(methods, key=lambda x: (x[0].lower(), x[1])))
        for (cname, clineno, methods) in classes
    ]

    return {'functions': functions, 'classes': classes}


def count_lines_binary(file_path):
    count = 0
    try:
        with open(file_path, 'rb') as f:
            for _ in f:
                count += 1
    except Exception:
        count = 0
    return count


def scan_project(root_path):
    root_abs = os.path.abspath(root_path)
    py_entries = []
    text_entries = []

    for dirpath, dirnames, filenames in os.walk(root_abs, topdown=True):
        # Skip hidden or underscored directories
        dirnames[:] = [d for d in dirnames if not should_skip(d)]

        for filename in sorted(filenames):
            if should_skip(filename):
                continue
            file_path = os.path.join(dirpath, filename)
            rel = _relpath(file_path, root_abs)
            lower = filename.lower()

            if lower.endswith('.py'):
                info = parse_python_file(file_path)
                py_entries.append((rel, info))
            elif lower.endswith('.txt') or lower.endswith('.enc'):
                line_count = count_lines_binary(file_path)
                text_entries.append((rel, line_count))

    py_entries.sort(key=lambda x: x[0].lower())
    text_entries.sort(key=lambda x: x[0].lower())

    # Build README content
    out_lines = []
    out_lines.append('# Project Scan')
    out_lines.append('')

    if py_entries:
        out_lines.append('## Python Files')
        out_lines.append('')
        for rel, info in py_entries:
            out_lines.append(f'- {rel}')
            if info['functions']:
                out_lines.append('  - Functions:')
                for name, lineno in info['functions']:
                    out_lines.append(f'    - {name} (line {lineno})')
            if info['classes']:
                out_lines.append('  - Classes:')
                for cname, clineno, methods in info['classes']:
                    out_lines.append(f'    - {cname} (line {clineno})')
                    if methods:
                        out_lines.append('      - Methods:')
                        for mname, mlineno in methods:
                            out_lines.append(f'        - {mname} (line {mlineno})')
            out_lines.append('')

    if text_entries:
        out_lines.append('## Text and Encoded Files')
        out_lines.append('')
        for rel, count in text_entries:
            out_lines.append(f'- {rel}: {count} lines')
        out_lines.append('')

    out_path = os.path.join(root_abs, 'README.md')
    with open(out_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(out_lines).rstrip() + '\n')

    print(f"README ditulis ke {out_path}")


if __name__ == '__main__':
    scan_project('.')
