"""Representative CSV workflows and malformed/unsafe-data regression cases."""
import unittest
from decimal import Decimal

from task_backend.csv_documents import csv_text, encode_csv, inspect_csv, transform_csv


def source(value):
    return value.encode('utf-8-sig')


class CsvDocumentsTests(unittest.TestCase):
    def test_chinese_multiline_quotes_leading_zero_and_literal_na_are_preserved(self):
        data = source('学号,说明,小时\r\n001,"讨论,实验\n补充",0.10\r\n002,NA,\r\n003,NaN,-2\r\n')
        result = inspect_csv(data, limit=2)
        self.assertEqual(result['rowCount'], 3)
        self.assertEqual(result['rows'], [['001', '讨论,实验\n补充', '0.10'], ['002', 'NA', '']])
        self.assertEqual(result['nextOffset'], 2)
        tail = inspect_csv(data, offset=2)
        self.assertEqual(tail['rows'], [['003', 'NaN', '-2']])
        self.assertIsNone(tail['nextOffset'])
        self.assertEqual(result['profiling'][2]['empty'], 1)

    def test_common_encodings_and_delimiters_are_explicit(self):
        for codec, delimiter in [('gb18030', ';'), ('utf-16', '\t'), ('utf-8-sig', ',')]:
            with self.subTest(codec=codec):
                raw = f'课程{delimiter}学号\r\n统计{delimiter}001\r\n'.encode(codec)
                result = inspect_csv(raw)
                self.assertEqual(result['encoding'], codec)
                self.assertEqual(result['delimiter'], delimiter)
                self.assertEqual(result['rows'], [['统计', '001']])

    def test_malformed_rows_duplicate_headers_empty_headers_and_quotes_fail_closed(self):
        for invalid in ['a,b\n1,2,3\n', 'a,b\n1\n', 'a,a\n1,2\n', 'a, a\n1,2\n',
                        ',b\n1,2\n', 'a,b\n"unclosed,2\n', 'a,b\n\n1,2\n']:
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    inspect_csv(source(invalid))

    def test_single_column_and_empty_table_are_valid(self):
        self.assertEqual(inspect_csv(source('学号\n001\n002\n'))['rows'], [['001'], ['002']])
        result = inspect_csv(source('a,b\n'))
        self.assertEqual(result['rowCount'], 0)
        self.assertEqual(result['rows'], [])
        output, report = transform_csv(source('a,b\n'), [{'op': 'groupby', 'by': [],
                                                       'aggregations': [{'column': 'a', 'op': 'count', 'output': 'count'}]}])
        self.assertEqual(inspect_csv(output)['rows'], [['0']])

    def test_student_cleaning_and_aggregation_are_actual_calculations(self):
        data = source('record_id,team,hours,status\n001,A,4,active\n002,A,6,active\n'
                      '002,A,6,active\n003,B,8,active\n004,B,12,active\n005,B,,active\n006,A,99,canceled\n')
        clean, report = transform_csv(data, [
            {'op': 'drop_duplicates', 'subset': ['record_id']},
            {'op': 'filter', 'column': 'status', 'operator': 'eq', 'value': 'active'},
            {'op': 'fill_missing', 'column': 'hours', 'value': '0'},
        ])
        self.assertEqual(report['rowsBefore'], 7)
        self.assertEqual(report['rowsAfter'], 5)
        self.assertEqual([r[0] for r in inspect_csv(clean)['rows']], ['001', '002', '003', '004', '005'])
        grouped, checked = transform_csv(clean, [{'op': 'groupby', 'by': ['team'],
            'aggregations': [{'column': 'hours', 'op': 'sum', 'output': 'total_hours'},
                             {'column': 'record_id', 'op': 'count', 'output': 'students'}]}])
        self.assertEqual(inspect_csv(grouped)['rows'], [['A', '10', '2'], ['B', '20', '3']])
        self.assertTrue(checked['checks']['reopened'])
        self.assertEqual(data.decode('utf-8-sig').count('002,A,6,active'), 2)

    def test_decimal_addition_and_multiplication_do_not_use_float_roundoff(self):
        data = source('id,a,b\n001,0.1,0.2\n002,1.25,2\n')
        output, _ = transform_csv(data, [
            {'op': 'derive', 'column': 'total', 'operator': 'add', 'left': {'column': 'a'}, 'right': {'column': 'b'}},
            {'op': 'derive', 'column': 'double', 'operator': 'multiply', 'left': {'column': 'total'}, 'right': {'value': '2'}},
        ])
        self.assertEqual(inspect_csv(output)['rows'], [['001', '0.1', '0.2', '0.3', '0.6'], ['002', '1.25', '2', '3.25', '6.5']])

    def test_division_rounding_is_explicit_and_zero_division_is_rejected(self):
        data = source('a,b\n1,3\n')
        step = {'op': 'derive', 'column': 'ratio', 'operator': 'divide', 'left': {'column': 'a'}, 'right': {'column': 'b'}}
        output, report = transform_csv(data, [step])
        self.assertEqual(inspect_csv(output)['rows'][0][2], '0.333333333333')
        self.assertEqual(report['divisionDecimalPlaces'], 12)
        self.assertTrue(any('ROUND_HALF_EVEN' in w for w in report['warnings']))
        with self.assertRaisesRegex(ValueError, '除数为零'):
            transform_csv(source('a,b\n1,0\n'), [step])

    def test_mean_min_max_and_count_handle_grouping(self):
        data = source('team,hours,note\nA,1,yes\nA,2,\nB,4,NA\n')
        output, report = transform_csv(data, [{'op': 'groupby', 'by': ['team'], 'aggregations': [
            {'column': 'hours', 'op': 'mean', 'output': 'mean'},
            {'column': 'hours', 'op': 'min', 'output': 'min'},
            {'column': 'hours', 'op': 'max', 'output': 'max'},
            {'column': 'note', 'op': 'count', 'output': 'count'},
        ]}])
        self.assertEqual(inspect_csv(output)['rows'], [['A', '1.5', '1', '2', '1'], ['B', '4', '4', '4', '1']])
        self.assertEqual(report['divisionDecimalPlaces'], 12)

    def test_numeric_conversion_requires_explicit_missing_cleanup(self):
        for value in ['', 'NaN', 'Infinity', '2,500', '1e9999', 'not-a-number']:
            with self.subTest(value=value):
                data = encode_csv(['value'], [[value]])
                with self.assertRaises(ValueError):
                    transform_csv(data, [{'op': 'groupby', 'by': [], 'aggregations': [
                        {'column': 'value', 'op': 'sum', 'output': 'sum'}]}])

    def test_numeric_sort_does_not_strip_source_number_formatting(self):
        data = source('id,value\n001,10.00\n002,2.00\n003,-1.50\n')
        output, _ = transform_csv(data, [
            {'op': 'filter', 'column': 'value', 'operator': 'gte', 'value': '0', 'numeric': True},
            {'op': 'sort', 'columns': ['value'], 'numeric': True},
        ])
        self.assertEqual(inspect_csv(output)['rows'], [['002', '2.00'], ['001', '10.00']])

    def test_text_filter_is_literal_not_regex(self):
        data = source('value\na.b\naxb\n')
        output, _ = transform_csv(data, [{'op': 'filter', 'column': 'value', 'operator': 'contains', 'value': '.'}])
        self.assertEqual(inspect_csv(output)['rows'], [['a.b']])

    def test_update_guards_old_value_and_column_selection(self):
        data = source('id,state,other\n001,draft,keep\n')
        output, _ = transform_csv(data, [
            {'op': 'update', 'row': 0, 'column': 'state', 'expected': 'draft', 'value': 'ready'},
            {'op': 'rename', 'mapping': {'state': 'status'}},
            {'op': 'select', 'columns': ['id', 'status']},
        ])
        self.assertEqual(inspect_csv(output)['rows'], [['001', 'ready']])
        with self.assertRaisesRegex(ValueError, '预期旧值'):
            transform_csv(data, [{'op': 'update', 'row': 0, 'column': 'state', 'expected': 'ready', 'value': 'sent'}])

    def test_formula_like_values_are_never_evaluated_and_outputs_are_safe_literals(self):
        raw = source('id,value\n001,=SUM(1:2)\n002,  @SUM(A1)\n003,-2.5\n004,+3.5\n005,-malicious\n')
        read = inspect_csv(raw)
        self.assertEqual(read['formulaLikeCells'], 3)
        self.assertEqual(read['rows'][0][1], '=SUM(1:2)')
        output, report = transform_csv(raw, [{'op': 'select', 'columns': ['id', 'value']}])
        self.assertEqual(inspect_csv(output)['rows'], [['001', "'=SUM(1:2)"], ['002', "'  @SUM(A1)"],
                                                     ['003', '-2.5'], ['004', '+3.5'], ['005', "'-malicious"]])
        self.assertEqual(report['formulaCellsProtected'], 3)
        again, repeated = transform_csv(output, [{'op': 'select', 'columns': ['id', 'value']}])
        self.assertEqual(again, output)
        self.assertEqual(repeated['formulaCellsProtected'], 0)

    def test_generated_csv_is_roundtrippable_and_does_not_coerce_objects(self):
        output = encode_csv(['id', 'amount', 'memo'], [['001', Decimal('0.10'), None], ['002', 7, 'a,b']])
        self.assertTrue(output.startswith(b'\xef\xbb\xbf'))
        self.assertEqual(inspect_csv(output)['rows'], [['001', '0.10', ''], ['002', '7', 'a,b']])
        for value in [float('nan'), float('inf'), 0.3, True, {'bad': 1}, ['a']]:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    encode_csv(['x'], [[value]])

    def test_unknown_columns_duplicate_outputs_and_code_are_rejected(self):
        data = source('a,b\n1,2\n')
        invalid = [
            {'op': 'eval', 'code': '__import__("os")'},
            {'op': 'select', 'columns': ['missing']},
            {'op': 'select', 'columns': [['a']]},
            {'op': 'rename', 'mapping': {'a': 'b'}},
            {'op': 'derive', 'column': 'a', 'operator': 'add', 'left': {'value': '1'}, 'right': {'value': '2'}},
            {'op': 'groupby', 'by': ['a'], 'aggregations': [{'column': 'b', 'op': 'sum', 'output': 'a'}]},
            {'op': 'filter', 'column': 'a', 'operator': 'eq', 'value': '1', 'numeric': 'yes'},
            {'op': 'sort', 'columns': ['a'], 'path': '/tmp/must-not-use'},
        ]
        for step in invalid:
            with self.subTest(step=step):
                with self.assertRaises(ValueError):
                    transform_csv(data, [step])

    def test_search_index_truncation_does_not_truncate_table_operations(self):
        output = encode_csv(['id', 'value'], [[str(i).zfill(6), 'x' * 120] for i in range(1000)])
        text, warnings = csv_text(output)
        self.assertLessEqual(len(text), 100000)
        self.assertTrue(any('搜索索引' in v for v in warnings))
        self.assertEqual(inspect_csv(output, offset=999)['rows'][0][0], '000999')
        counted, _ = transform_csv(output, [{'op': 'groupby', 'by': [], 'aggregations': [
            {'column': 'id', 'op': 'count', 'output': 'count'}]}])
        self.assertEqual(inspect_csv(counted)['rows'], [['1000']])

    def test_size_shape_and_pagination_limits(self):
        for data in [b'', b'x' * (10 * 1024 * 1024 + 1), b'a\n\x00\n']:
            with self.assertRaises(ValueError):
                inspect_csv(data)
        with self.assertRaises(ValueError):
            encode_csv([f'c{i}' for i in range(101)], [])
        with self.assertRaises(ValueError):
            encode_csv(['a', 'b'], [['1']])
        for arguments in [dict(offset=-1), dict(offset=2), dict(limit=0), dict(limit=101), dict(offset=True)]:
            with self.assertRaises(ValueError):
                inspect_csv(source('a\n1\n'), **arguments)


if __name__ == '__main__':
    unittest.main()
