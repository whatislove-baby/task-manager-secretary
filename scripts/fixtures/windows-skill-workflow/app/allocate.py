"""Synthetic inventory allocation; independent expected CSVs live in examples/.

This fixed fixture demonstrates importing existing code. It is not a third-party
production skill and must not be described as a live model authoring result.
"""
import csv
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import re


class BusinessInputError(ValueError):
    pass


def rows(path, columns):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != columns:
            raise BusinessInputError('INVALID_COLUMNS: ' + path.name)
        result = list(reader)
    if not result or any(None in row or any(value is None for value in row.values()) for row in result):
        raise BusinessInputError('INVALID_ROWS: ' + path.name)
    return result


def integer(raw, minimum):
    if not re.fullmatch(r'[0-9]+', raw) or int(raw) < minimum:
        raise BusinessInputError('INVALID_QUANTITY')
    return int(raw)


def allocate(source, output):
    orders = rows(source / 'orders.csv', ['PO', 'Line', 'SKU', 'Qty', 'UnitPriceUSD'])
    stocks = rows(source / 'stock.csv', ['SKU', 'Available'])
    available = {}
    for row in stocks:
        sku = row['SKU']
        if not sku or sku in available:
            raise BusinessInputError('DUPLICATE_OR_MISSING_SKU')
        available[sku] = integer(row['Available'], 0)
    validated = []
    for row in orders:
        if any(not row[name] for name in ('PO', 'Line', 'SKU')):
            raise BusinessInputError('MISSING_IDENTIFIER')
        if not row['UnitPriceUSD'].strip():
            raise BusinessInputError('MISSING_PRICE')
        try:
            price = Decimal(row['UnitPriceUSD'])
        except InvalidOperation:
            raise BusinessInputError('INVALID_PRICE') from None
        if not price.is_finite() or price < 0:
            raise BusinessInputError('INVALID_PRICE')
        validated.append((row, integer(row['Qty'], 1), price))
    result = []
    for row, quantity, price in validated:
        allocated = min(quantity, available.get(row['SKU'], 0))
        available[row['SKU']] = available.get(row['SKU'], 0) - allocated
        shortage = quantity - allocated
        value = (shortage * price).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
        result.append([row['PO'], row['Line'], row['SKU'], quantity, allocated, shortage, format(value, '.2f')])
    # No output is opened until every row has passed validation.
    with (output / 'allocation.csv').open('w', encoding='utf-8', newline='') as stream:
        writer = csv.writer(stream, lineterminator='\n')
        writer.writerow(['PO', 'Line', 'SKU', 'Qty', 'Allocated', 'Shortage', 'ShortageValueUSD'])
        writer.writerows(result)
