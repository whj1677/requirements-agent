"""Small, shared reading format for saved prose; no generated business facts.

Only explicit Markdown headings and consecutive pipe rows are interpreted. Unrecognized
syntax stays verbatim. Canonical blocks, references and source text are retained
by document_reader, while web, Markdown and Word consume the same reading nodes.
"""
import re


def has_layout(text):
    if re.search(r'(?m)^#{2,4} .+',text):return True
    lines=str(text).splitlines()
    for first,second in zip(lines,lines[1:]):
        columns,following=_cells(first),_cells(second)
        if columns and following and len(columns)>=2 and len(columns)==len(following):return True
    return False


def prose_indent(text):
    return not bool(re.match(r'^\s*(?:\d+[.、]\s*|AC\d+\b|[-•]\s|[A-Za-z_][A-Za-z_\d]*\s*=)',text))


def covered_prose(statement, value):
    """Only remove an exact standalone passage, never a phrase inside negation."""
    boundaries='\n\r。！？；;'
    start=0
    while value:
        index=statement.find(value,start)
        if index<0:return False
        end=index+len(value)
        if (index==0 or statement[index-1] in boundaries) and (
                end==len(statement) or value[-1] in boundaries or statement[end] in boundaries):
            return True
        start=index+1
    return False


def _cells(line):
    if not line.startswith('|') or not line.endswith('|'):
        return None
    return [part.strip().replace('\\|', '|') for part in re.split(r'(?<!\\)\|',line[1:-1])]


def prose_nodes(text, **metadata):
    """Interpret an explicit layout, otherwise keep newline-separated paragraphs."""
    lines=str(text).splitlines()
    explicit_layout=has_layout(str(text))
    nodes=[]; index=0; paragraph=[]
    def flush():
        if paragraph:
            value='\n'.join(paragraph)
            nodes.append(dict(metadata,kind='paragraph',text=value,first_line_indent=prose_indent(value)))
            paragraph.clear()
    while index<len(lines):
        line=lines[index]
        title=re.fullmatch(r'#{2,4} (\S.*)',line)
        columns=_cells(line)
        separator=_cells(lines[index+1]) if columns and index+1<len(lines) else None
        has_separator=bool(separator and all(re.fullmatch(r':?-{3,}:?',cell) for cell in separator))
        if title:
            flush();nodes.append(dict(metadata,kind='subheading',text=title[1]));index+=1
        elif columns and len(columns)>=2 and separator and len(columns)==len(separator):
            # Models sometimes omit Markdown's delimiter row. Consecutive pipe
            # rows still have explicit cell boundaries; preserve every cell.
            rows=[];end=index+(2 if has_separator else 1)
            while end<len(lines):
                cells=_cells(lines[end])
                if not cells or len(cells)!=len(columns):break
                rows.append(cells);end+=1
            if not rows:
                paragraph.append(line);index+=1;continue
            flush()
            # Keep short codes compact, give conditions and results room to wrap.
            lengths=[min(36,max(len(row[n]) for row in [columns,*rows])) for n in range(len(columns))]
            weights=[max(8,value) for value in lengths];total=sum(weights)
            weights=[value/total for value in weights]
            if len(weights)==3 and weights[0]<.18:
                remaining=sum(weights[1:])
                weights=[.18,*[value*.82/remaining for value in weights[1:]]]
            nodes.append(dict(metadata,kind='table',title='',text='',columns=columns,rows=rows,
                              column_weights=weights,table_role='prose'))
            index=end
        elif not line.strip():
            flush();index+=1
        else:
            # Explicit numbered paragraphs already carry identity. Do not number
            # their sentences again or hide a multi-page AC in one table cell.
            if (re.match(r'^(?:\d+[.、] |AC\d+\b)',line)
                    or explicit_layout and paragraph and paragraph[-1].endswith(('。','！','？'))):flush()
            paragraph.append(line);index+=1
    flush()
    return nodes or [dict(metadata,kind='paragraph',text=str(text),first_line_indent=prose_indent(str(text)))]
