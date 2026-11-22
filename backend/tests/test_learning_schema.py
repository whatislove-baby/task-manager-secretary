"""The live model sees the writable contract, not an untyped JSON placeholder."""
from copy import deepcopy
import unittest

from pydantic import ValidationError

from task_backend.agent_tools import build_tools
from task_backend.learning_schema import LearningProposal, LearningRecipe


def example():
    return dict(title='Acknowledgement',kind='table_workflow',scope=dict(kind='customer',label='Harbor',emails=['orders@harbor.example']),
        instruction='Preserve PO and customer part strings, one row per input. Ask if required date is missing.',
        sources=[dict(kind='file',id='input-file')],exampleFileId='input-file',
        recipe=dict(inputColumns=['PO','Date'],mapping={'PO No':'PO','Required Date':'Date'},
                    outputColumns=['PO No','Required Date'],requiredValues=['PO No','Required Date'],numericColumns=[],outputFormat='xlsx'))


class LearningWireSchema(unittest.TestCase):
    def test_actual_tool_exposes_strict_nested_writable_schema(self):
        tool=next(t for t in build_tools(object()) if t.name=='propose_learning')
        schema=tool.tool_call_schema.model_json_schema()
        value=schema['properties']['value']
        self.assertIn('$ref',value)
        definitions=schema['$defs'];proposal=definitions['LearningProposal']
        self.assertFalse(proposal['additionalProperties'])
        for name in ('LearningScope','LearningSource','LearningRecipe','LearningConcat','LearningCommercial','DeriveOperation'):
            self.assertFalse(definitions[name]['additionalProperties'],name)
        self.assertEqual(set(definitions['LearningSource']['properties']),{'kind','id'})
        self.assertNotIn('teaching',proposal['properties'])
        self.assertNotIn('versionHash',proposal['properties'])
        self.assertNotIn('runtime',schema['properties'])
        self.assertIn('NOT scopeId',proposal['properties']['parentId']['description'])
        self.assertIn('INPUT XLSX',definitions['LearningRecipe']['properties']['sheetName']['description'])

    def test_defaults_and_note_inputs_match_service_contract(self):
        raw=example();value=LearningProposal.model_validate(raw).model_dump(exclude_none=True)
        self.assertEqual(value['recipe']['optionalInputColumns'],[])
        self.assertNotIn('parentId',value);self.assertNotIn('sheetName',value['recipe'])
        note={k:v for k,v in raw.items() if k not in ('recipe','exampleFileId')};note['kind']='note'
        self.assertNotIn('recipe',LearningProposal.model_validate(note).model_dump(exclude_none=True))
        with self.assertRaisesRegex(ValidationError,'recipe and exampleFileId'):
            LearningProposal.model_validate(dict(note,kind='table_workflow'))
        with self.assertRaisesRegex(ValidationError,'note must omit'):
            LearningProposal.model_validate(dict(raw,kind='note'))

    def test_readonly_metadata_is_rejected_instead_of_silently_dropped(self):
        raw=example();raw['sources'][0].update(name='old file',sha256='server-hash',role='reference-output')
        with self.assertRaises(ValidationError) as raised:LearningProposal.model_validate(raw)
        locations={e['loc'] for e in raised.exception.errors()}
        self.assertIn(('sources',0,'sha256'),locations)
        self.assertIn(('sources',0,'role'),locations)
        raw=example();raw['teaching']={'source':'invented'}
        with self.assertRaises(ValidationError) as raised:LearningProposal.model_validate(raw)
        self.assertIn(('teaching',),{e['loc'] for e in raised.exception.errors()})
        raw=example();raw['parentId']='customer-0123456789'
        with self.assertRaises(ValidationError) as raised:LearningProposal.model_validate(raw)
        self.assertIn(('parentId',),{e['loc'] for e in raised.exception.errors()})

    def test_operations_are_finite_typed_and_decimal_strings_stay_exact(self):
        raw=example();raw['recipe']['operations']=[dict(op='derive',column='Amount',operator='multiply',left=dict(column='Qty'),right=dict(value='0.095'))]
        value=LearningProposal.model_validate(raw).model_dump(exclude_none=True)
        self.assertEqual(value['recipe']['operations'][0]['right']['value'],'0.095')
        cases=[dict(op='eval',code='2+2'),dict(op='derive',column='Amount',operator='multiply',left=dict(column='Qty'),right=dict(value=0.095)),
               dict(op='derive',column='Amount',operator='multiply',left=dict(column='Qty',value='2'),right=dict(value='0.095'))]
        for operation in cases:
            with self.subTest(operation=operation):
                candidate=deepcopy(raw);candidate['recipe']['operations']=[operation]
                with self.assertRaises(ValidationError):LearningProposal.model_validate(candidate)
        raw=example();raw['recipe']['allowExtraColumns']='false'
        with self.assertRaises(ValidationError):LearningProposal.model_validate(raw)

    def test_all_supported_operation_shapes_survive_dump_without_losing_explicit_blank(self):
        operations=[dict(op='drop_duplicates'),dict(op='fill_missing',column='A',value=None),
            dict(op='filter',column='A',operator='is_empty'),dict(op='filter',column='A',operator='eq',value=None),
            dict(op='sort',columns=['A']),dict(op='select',columns=['A']),dict(op='rename',mapping={'A':'B'}),
            dict(op='groupby',by=[],aggregations=[dict(column='A',op='sum',output='Total')]),
            dict(op='update',row=0,column='A',expected=None,value='new')]
        raw=example()['recipe'];raw['operations']=operations
        dumped=LearningRecipe.model_validate(raw).model_dump(exclude_none=True)['operations']
        self.assertEqual(dumped[1]['value'],'');self.assertEqual(dumped[3]['value'],'');self.assertEqual(dumped[-1]['expected'],'')
        self.assertNotIn('subset',dumped[0])
        raw['operations']=[dict(op='filter',column='A',operator='eq')]
        with self.assertRaisesRegex(ValidationError,'requires value'):LearningRecipe.model_validate(raw)


if __name__=='__main__':unittest.main()
