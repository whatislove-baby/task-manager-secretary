"""Bounded CSV documents and deterministic dataframe operations.

Cells are strings until an explicitly numeric operation requests conversion.
No Python expressions, formulas, URLs or filesystem paths are evaluated. Numeric
calculations use Decimal; pandas owns selection, grouping and row operations.
"""
from __future__ import annotations

import csv
from decimal import Decimal, InvalidOperation, localcontext, ROUND_HALF_EVEN
from io import StringIO
import re

import pandas as pd

MAX_BYTES = 10 * 1024 * 1024
MAX_ROWS = 100_000
MAX_COLUMNS = 100
MAX_CELLS = 1_000_000
MAX_INDEX_TEXT = 100_000
MAX_CELL = 100_000
DIVISION_PLACES = 12
NUMERIC = re.compile(r'^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$', re.ASCII)


def _cell(value):
    if not isinstance(value, str):
        if value is None:
            return ''
        # Do not silently convert float NaN/Infinity, booleans, objects or arrays.
        if type(value) is int:
            value = str(value)
        elif isinstance(value, Decimal) and value.is_finite():
            value = str(value)
        else:
            raise ValueError('CSV 单元格须为文字、整数或空值；小数请传十进制字符串')
    if len(value) > MAX_CELL or re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', value):
        raise ValueError('CSV 单元格过长或包含不支持的控制字符')
    return value


def _headers(columns):
    if not isinstance(columns, list) or not 1 <= len(columns) <= MAX_COLUMNS:
        raise ValueError('CSV 须有 1 至 100 列')
    if any(not isinstance(v, str) or not v.strip() or len(v) > 200 for v in columns):
        raise ValueError('CSV 列名不能为空，且不能超过 200 字符')
    for value in columns:
        _cell(value)
    if len({v.strip() for v in columns}) != len(columns):
        raise ValueError('CSV 包含重复列名，请先明确每一列的名称')
    return columns


def _decode(data):
    if not isinstance(data, bytes) or not data or len(data) > MAX_BYTES:
        raise ValueError('CSV 不能为空，单个文件不能超过 10 MB')
    if data.startswith((b'\xff\xfe', b'\xfe\xff')):
        encoding = 'utf-16'
    else:
        encoding = 'utf-8-sig'
    try:
        value = data.decode(encoding)
    except UnicodeDecodeError:
        try:
            value = data.decode('gb18030')
            encoding = 'gb18030'
        except UnicodeDecodeError:
            raise ValueError('无法识别 CSV 编码，请另存为 UTF-8 CSV') from None
    _cell_controls = re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', value)
    if _cell_controls:
        raise ValueError('CSV 包含无效控制字符；无 BOM 的 UTF-16 文件请另存为 UTF-8')
    return value, encoding


def _numeric(value, column=''):
    value = str(value).strip()
    if not value or len(value) > 128 or not NUMERIC.fullmatch(value):
        raise ValueError(f'列 {column or "数值"} 含空值或非数值，请先明确清洗规则')
    try:
        number = Decimal(value)
    except InvalidOperation:
        raise ValueError(f'列 {column or "数值"} 无法转换为十进制数') from None
    if not number.is_finite() or len(number.as_tuple().digits) > 30 or abs(number.adjusted()) > 30:
        raise ValueError(f'列 {column or "数值"} 的数值范围或精度超出限制')
    return number


def _format_number(value, *, division=False):
    if division:
        value = value.quantize(Decimal(1).scaleb(-DIVISION_PLACES), rounding=ROUND_HALF_EVEN)
    result = format(value, 'f')
    if '.' in result:
        result = result.rstrip('0').rstrip('.')
    return '0' if result in ('-0', '') else result


def _formula(value):
    cleaned = value.lstrip(' \t\r\n\ufeff')
    # Signed decimal values are data, not spreadsheet formulas.
    return bool(cleaned and cleaned[0] in '=+-@' and not NUMERIC.fullmatch(cleaned))


def _read(data):
    content, encoding = _decode(data)
    warnings = []
    if encoding != 'utf-8-sig':
        warnings.append(f'输入编码识别为 {encoding}；输出统一为 UTF-8 BOM')
    # Detect only permitted delimiters. Sniffer may fail on inconsistent rows;
    # parse the header separately so the subsequent strict pass reports them.
    sample = content[:65536]
    try:
        delimiter = csv.Sniffer().sniff(sample, delimiters=',;\t').delimiter
    except csv.Error:
        try:
            first = next(csv.reader(StringIO(content), strict=True), [])
            if len(first) > 1:
                delimiter = ','
            else:
                candidates = [(d, next(csv.reader(StringIO(content), delimiter=d, strict=True), []))
                              for d in (';', '\t')]
                viable = [(d, h) for d, h in candidates if len(h) > 1]
                delimiter = max(viable, key=lambda v: len(v[1]))[0] if viable else ','
        except csv.Error:
            raise ValueError('CSV 引号或分隔符不完整，无法读取列名') from None
    try:
        reader = csv.reader(StringIO(content, newline=''), delimiter=delimiter, strict=True)
        columns = _headers(next(reader, []))
        rows = []
        formula_count = sum(_formula(value) for value in columns)
        for row in reader:
            if len(row) != len(columns):
                raise ValueError(f'CSV 第 {reader.line_num} 行列数不一致，未忽略或补齐该行')
            if len(rows) >= MAX_ROWS or (len(rows) + 1) * len(columns) > MAX_CELLS:
                raise ValueError('CSV 超过 100000 行或 1000000 个单元格，请拆分处理')
            rows.append([_cell(value) for value in row])
            formula_count += sum(_formula(value) for value in row)
    except csv.Error:
        raise ValueError('CSV 格式损坏或引号未闭合，未部分导入') from None
    if formula_count:
        warnings.append(f'发现 {formula_count} 个可能被电子表格当作公式的值；仅按文字读取，导出时加前导单引号保护')
    frame = pd.DataFrame(rows, columns=columns, dtype=object)
    return frame, dict(encoding=encoding, delimiter=delimiter, warnings=warnings,
                      formulaLikeCells=formula_count)


def inspect_csv(data: bytes, offset=0, limit=30):
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('CSV 预览范围无效；每次最多读取 100 行')
    frame, info = _read(data)
    count = len(frame)
    if offset > count:
        raise ValueError('CSV 读取起点超出行数')
    profiling = []
    for column in frame.columns:
        values = frame[column]
        nonempty = values[values != '']
        numeric = 0
        for value in nonempty:
            try:
                _numeric(value, column)
                numeric += 1
            except ValueError:
                pass
        profiling.append(dict(column=column, empty=int((values == '').sum()),
                              unique=int(values.nunique(dropna=False)), numericValues=numeric,
                              numericCandidate=bool(len(nonempty)) and numeric == len(nonempty),
                              sourceType='text'))
    end = min(offset + limit, count)
    return dict(columns=list(frame.columns), rowCount=count, columnCount=len(frame.columns),
                rows=frame.iloc[offset:end].values.tolist(), offset=offset,
                nextOffset=end if end < count else None, profiling=profiling, **info)


def csv_text(data: bytes):
    """Bounded search text only; the original and table tools retain every row."""
    frame, info = _read(data)
    start = f'CSV 表格：{len(frame)} 行，{len(frame.columns)} 列。编码 {info["encoding"]}。\n'
    output = StringIO(newline='')
    writer = csv.writer(output, lineterminator='\n')
    writer.writerow(frame.columns)
    prefix = start + output.getvalue()
    warnings = list(info['warnings'])
    parts = [prefix]
    size = len(prefix)
    for row in frame.itertuples(index=False, name=None):
        output.seek(0)
        output.truncate(0)
        writer.writerow(row)
        line = output.getvalue()
        if size + len(line) > MAX_INDEX_TEXT - 100:
            parts.append('\n【搜索文字已截短；原 CSV 完整保留，请使用表格工具分行读取与计算】')
            warnings.append('CSV 搜索索引仅包含前部行；原文件及表格计算使用完整数据')
            break
        parts.append(line)
        size += len(line)
    return ''.join(parts), warnings


def _encode(columns, rows):
    columns = _headers(columns)
    if not isinstance(rows, list) or len(rows) > MAX_ROWS or len(rows) * len(columns) > MAX_CELLS:
        raise ValueError('CSV 数据须为行数组且不超过 100000 行、1000000 个单元格')
    output = StringIO(newline='')
    writer = csv.writer(output, lineterminator='\r\n')
    protected = 0
    size = 0
    chunks = [b'\xef\xbb\xbf']
    for row in [columns, *rows]:
        if not isinstance(row, (list, tuple)) or len(row) != len(columns):
            raise ValueError('CSV 数据行的列数与表头不一致')
        values = [_cell(v) for v in row]
        protected += sum(_formula(v) for v in values)
        values = ["'" + v if _formula(v) else v for v in values]
        output.seek(0)
        output.truncate(0)
        writer.writerow(values)
        data = output.getvalue().encode('utf-8')
        size += len(data)
        if size + 3 > MAX_BYTES:
            raise ValueError('生成 CSV 超过 10 MB，请缩小处理范围')
        chunks.append(data)
    return b''.join(chunks), protected


def encode_csv(columns, rows):
    """Create UTF-8 BOM CSV. Formula-like values become explicit text literals."""
    return _encode(columns, rows)[0]


def _shape(value, allowed, required=()):
    if not isinstance(value, dict) or set(value) - set(allowed) or not set(required) <= set(value):
        raise ValueError('CSV 操作参数不完整或包含未知字段')


def _columns(frame, value, *, allow_empty=False):
    if not isinstance(value, list) or any(not isinstance(v, str) for v in value) or (not value and not allow_empty) or len(set(value)) != len(value):
        raise ValueError('请提供不重复的 CSV 列名数组')
    if any(not isinstance(v, str) or v not in frame.columns for v in value):
        raise ValueError('CSV 操作引用了不存在的列')
    return value


def _column(frame, value):
    _columns(frame, [value])
    return value


def _boolean(value):
    if type(value) is not bool:
        raise ValueError('CSV 布尔选项无效')
    return value


def transform_csv(data: bytes, operations: list[dict]):
    """Execute a small explicit operation language; never evaluate expressions.

    op schemas: drop_duplicates(subset), fill_missing(column,value),
    filter(column,operator,value?,numeric?), sort(columns,ascending?,numeric?),
    select(columns), rename(mapping), groupby(by,aggregations),
    derive(column,operator,left,right), update(row,column,expected,value).
    derive operands are {column:name} or {value:decimal-string}; division and
    mean use 12 fractional places with ROUND_HALF_EVEN, reported to the caller.
    """
    if not isinstance(operations, list) or not 1 <= len(operations) <= 30:
        raise ValueError('请提供 1 至 30 个 CSV 处理步骤')
    frame, info = _read(data)
    before = len(frame)
    changes = []
    rounded = False
    with localcontext() as context:
        context.prec = 100
        for step in operations:
            if not isinstance(step, dict):
                raise ValueError('CSV 操作必须是对象')
            op = step.get('op')
            previous = len(frame)
            if op == 'drop_duplicates':
                _shape(step, ['op', 'subset'], ['op'])
                subset = _columns(frame, step['subset']) if 'subset' in step else None
                frame = frame.drop_duplicates(subset=subset, keep='first').reset_index(drop=True)
            elif op == 'fill_missing':
                _shape(step, ['op', 'column', 'value'], ['op', 'column', 'value'])
                column = _column(frame, step['column'])
                frame.loc[frame[column] == '', column] = _cell(step['value'])
            elif op == 'filter':
                _shape(step, ['op', 'column', 'operator', 'value', 'numeric'], ['op', 'column', 'operator'])
                column = _column(frame, step['column'])
                operator = step['operator']
                series = frame[column]
                if operator in ('is_empty', 'not_empty'):
                    mask = series == '' if operator == 'is_empty' else series != ''
                else:
                    if 'value' not in step:
                        raise ValueError('CSV 筛选缺少比较值')
                    numeric = _boolean(step.get('numeric', False))
                    value = _cell(step['value'])
                    if numeric:
                        if operator == 'contains':
                            raise ValueError('contains 不支持数值模式')
                        series = series.map(lambda v: _numeric(v, column))
                        value = _numeric(value, column)
                    comparisons = {'eq': lambda: series == value, 'ne': lambda: series != value,
                                   'gt': lambda: series > value, 'gte': lambda: series >= value,
                                   'lt': lambda: series < value, 'lte': lambda: series <= value,
                                   'contains': lambda: series.str.contains(value, regex=False)}
                    if operator not in comparisons:
                        raise ValueError('不支持此 CSV 筛选运算')
                    mask = comparisons[operator]()
                frame = frame.loc[mask].reset_index(drop=True)
            elif op == 'sort':
                _shape(step, ['op', 'columns', 'ascending', 'numeric'], ['op', 'columns'])
                columns = _columns(frame, step['columns'])
                ascending = _boolean(step.get('ascending', True))
                numeric = _boolean(step.get('numeric', False))
                frame = frame.sort_values(columns, ascending=ascending, kind='stable',
                                          key=(lambda s: s.map(lambda v: _numeric(v, s.name))) if numeric else None).reset_index(drop=True)
            elif op == 'select':
                _shape(step, ['op', 'columns'], ['op', 'columns'])
                frame = frame[_columns(frame, step['columns'])].copy()
            elif op == 'rename':
                _shape(step, ['op', 'mapping'], ['op', 'mapping'])
                mapping = step['mapping']
                if not isinstance(mapping, dict) or not mapping:
                    raise ValueError('CSV 重命名需要列名映射')
                _columns(frame, list(mapping))
                _headers([mapping.get(v, v) for v in frame.columns])
                frame = frame.rename(columns=mapping)
            elif op == 'groupby':
                _shape(step, ['op', 'by', 'aggregations'], ['op', 'by', 'aggregations'])
                by = _columns(frame, step['by'], allow_empty=True)
                specs = step['aggregations']
                if not isinstance(specs, list) or not 1 <= len(specs) <= MAX_COLUMNS:
                    raise ValueError('CSV 汇总须有 1 至 100 项计算')
                for spec in specs:
                    _shape(spec, ['column', 'op', 'output'], ['column', 'op', 'output'])
                    _column(frame, spec['column'])
                    if spec['op'] not in ('sum', 'mean', 'min', 'max', 'count'):
                        raise ValueError('不支持此 CSV 汇总运算')
                names = _headers(by + [spec['output'] for spec in specs])
                groups = frame.groupby(by[0] if len(by) == 1 else by, sort=False, dropna=False) if by else [((), frame)]
                rows = []
                for key, group in groups:
                    row = ([key] if len(by) == 1 else list(key)) if by else []
                    for spec in specs:
                        values = group[spec['column']]
                        operation = spec['op']
                        if operation == 'count':
                            row.append(str(int((values != '').sum())))
                            continue
                        numbers = [_numeric(v, spec['column']) for v in values]
                        if not numbers:
                            raise ValueError('CSV 空数据无法进行此数值汇总')
                        if operation in ('sum', 'mean'):
                            result = sum(numbers, Decimal(0))
                            if operation == 'mean':
                                result /= Decimal(len(numbers))
                                rounded = True
                        else:
                            result = min(numbers) if operation == 'min' else max(numbers)
                        row.append(_format_number(result, division=operation == 'mean'))
                    rows.append(row)
                frame = pd.DataFrame(rows, columns=names, dtype=object)
            elif op == 'derive':
                _shape(step, ['op', 'column', 'operator', 'left', 'right'], ['op', 'column', 'operator', 'left', 'right'])
                column = step['column']
                if column in frame.columns:
                    raise ValueError('派生列名称已存在，未覆盖原列')
                _headers(list(frame.columns) + [column])
                operands = []
                for side in ('left', 'right'):
                    operand = step[side]
                    _shape(operand, ['column', 'value'])
                    if len(operand) != 1:
                        raise ValueError('派生运算每侧须指定一列或一个数值')
                    if 'column' in operand:
                        name = _column(frame, operand['column'])
                        operands.append([_numeric(v, name) for v in frame[name]])
                    else:
                        operands.append([_numeric(_cell(operand['value']))] * len(frame))
                operator = step['operator']
                if operator not in ('add', 'subtract', 'multiply', 'divide'):
                    raise ValueError('派生列只支持加、减、乘、除')
                results = []
                for left, right in zip(*operands):
                    if operator == 'divide' and right == 0:
                        raise ValueError('CSV 派生计算遇到除数为零，未生成文件')
                    number = {'add': lambda: left + right, 'subtract': lambda: left - right,
                              'multiply': lambda: left * right, 'divide': lambda: left / right}[operator]()
                    results.append(_format_number(number, division=operator == 'divide'))
                rounded |= operator == 'divide'
                frame[column] = results
            elif op == 'update':
                _shape(step, ['op', 'row', 'column', 'expected', 'value'], ['op', 'row', 'column', 'expected', 'value'])
                row = step['row']
                column = _column(frame, step['column'])
                if type(row) is not int or not 0 <= row < len(frame):
                    raise ValueError('CSV 行号无效；数据行从 0 开始，不包含表头')
                if frame.at[row, column] != _cell(step['expected']):
                    raise ValueError('CSV 单元格与预期旧值不符，未修改文件')
                frame.at[row, column] = _cell(step['value'])
            else:
                raise ValueError('未知 CSV 操作；不支持执行脚本或公式')
            changes.append(dict(op=op, rowsBefore=previous, rowsAfter=len(frame)))
        output, protected = _encode(list(frame.columns), frame.values.tolist())
    reopened, _ = _read(output)
    if len(reopened) != len(frame) or len(reopened.columns) != len(frame.columns):
        raise ValueError('CSV 输出重新读取检查失败，未返回文件')
    warnings = list(info['warnings'])
    if protected:
        warnings.append(f'输出中的 {protected} 个公式样式值已加前导单引号，作为普通文字打开')
    if rounded:
        warnings.append('均值和除法最多保留 12 位小数，使用银行家舍入 ROUND_HALF_EVEN')
    report = dict(rowsBefore=before, rowsAfter=len(frame), columns=list(reopened.columns),
                  operations=changes, warnings=warnings, formulaCellsProtected=protected,
                  calculation='Decimal arithmetic; no expressions evaluated',
                  divisionDecimalPlaces=DIVISION_PLACES if rounded else None,
                  checks=dict(reopened=True, rowCountMatches=True, columnCountMatches=True,
                              noFormulaExecution=True), encoding='utf-8-sig')
    return output, report
