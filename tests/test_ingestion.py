import tempfile
import unittest
from zipfile import BadZipFile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from docx import Document as WordDocument
from langchain_core.documents import Document
from pypdf import PdfWriter
from pypdf.errors import PdfReadError
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from src import ingestion, vectorstore
from src.config import Settings


class IngestionTests(unittest.TestCase):
    def setUp(self):
        temporary = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(temporary)
        self.store = self.enterContext(patch('src.ingestion.get_vector_store')).return_value
        self.enterContext(patch('src.ingestion.get_settings', return_value=Settings(
            pinecone_namespace='test-runbooks')))

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
        return path

    def test_utf8_text_and_markdown_metadata(self):
        for name in ['runbook.TXT', 'runbook.MD']:
            with self.subTest(name=name):
                path = self.write(name, 'Incident café\nRestart safely.')
                docs = ingestion.load_file(path)
                self.assertEqual([d.page_content for d in docs], ['Incident café\nRestart safely.'])
                self.assertEqual(docs[0].metadata, {
                    'source': str(path), 'title': name, 'document_name': name})

    def test_docx_paragraph_extraction(self):
        path = self.root / 'runbook.docx'
        doc = WordDocument()
        doc.add_paragraph('Check deployment')
        doc.add_paragraph('   ')
        doc.add_paragraph('Roll back safely')
        doc.save(path)
        loaded = ingestion.load_file(path)
        self.assertEqual(loaded[0].page_content, 'Check deployment\nRoll back safely')
        self.assertEqual(loaded[0].metadata['document_name'], path.name)

    def test_pdf_text_and_page_metadata(self):
        path = self.root / 'runbook.pdf'
        writer = PdfWriter()
        for message in ['Check deployment', 'Roll back safely']:
            page = writer.add_blank_page(width=300, height=300)
            font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
                                     NameObject('/Subtype'): NameObject('/Type1'),
                                     NameObject('/BaseFont'): NameObject('/Helvetica')})
            page[NameObject('/Resources')] = DictionaryObject({
                NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
            stream = DecodedStreamObject()
            stream.set_data(f'BT /F1 12 Tf 20 200 Td ({message}) Tj ET'.encode('ascii'))
            page[NameObject('/Contents')] = writer._add_object(stream)
        writer.write(path)
        docs = ingestion.load_file(path)
        self.assertEqual([d.page_content.strip() for d in docs], ['Check deployment', 'Roll back safely'])
        self.assertEqual([d.metadata['page'] for d in docs], [0, 1])
        self.assertTrue(all(d.metadata['document_name'] == path.name for d in docs))

    def test_unsupported_missing_and_malformed_files_never_connect(self):
        cases = [(self.write('bad.csv', 'data'), ValueError),
                 (self.root / 'missing.md', RuntimeError),
                 (self.write('broken.pdf', 'not a pdf'), PdfReadError),
                 (self.write('broken.docx', 'not a zip'), BadZipFile)]
        for path, error in cases:
            with self.subTest(path=path), self.assertRaises(error):
                ingestion.ingest_file(path)
        ingestion.get_vector_store.assert_not_called()

    def test_empty_extraction_never_connects(self):
        for name, content in [('empty.md', ''), ('spaces.txt', ' \n\t')]:
            with self.subTest(name=name):
                self.assertEqual(ingestion.ingest_file(self.write(name, content)), 0)
        blank_pdf = self.root / 'blank.pdf'
        writer = PdfWriter()
        writer.add_blank_page(width=300, height=300)
        writer.write(blank_pdf)
        empty_docx = self.root / 'empty.docx'
        WordDocument().save(empty_docx)
        for path in [blank_pdf, empty_docx]:
            self.assertEqual(ingestion.ingest_file(path), 0)
        ingestion.get_vector_store.assert_not_called()

    def test_store_setup_failure_propagates_before_writes(self):
        ingestion.get_vector_store.side_effect = RuntimeError('index unavailable')
        with self.assertRaisesRegex(RuntimeError, 'index unavailable'):
            ingestion.ingest_file(self.write('runbook.md', 'Restart safely'))
        self.store.add_documents.assert_not_called()

    def test_real_splitter_bounds_overlap_metadata_and_repeat_ids(self):
        content = ''.join(f'operation-{i:04d} ' for i in range(300))
        path = self.write('first/runbook.md', content)
        count = ingestion.ingest_file(path)
        chunks = self.store.add_documents.call_args.args[0]
        ids = self.store.add_documents.call_args.kwargs['ids']
        self.assertGreater(count, 1)
        self.assertEqual(count, len(chunks))
        self.assertEqual(len(set(ids)), count)
        for i, chunk in enumerate(chunks):
            self.assertLessEqual(len(chunk.page_content), 800)
            self.assertEqual(chunk.metadata['chunk_index'], i)
            self.assertEqual(chunk.metadata['knowledge_base'], 'test-runbooks')
            self.assertEqual(chunk.metadata['document_name'], path.name)
        self.assertTrue(any(chunks[0].page_content.endswith(chunks[1].page_content[:n])
                            for n in range(40, 161)))
        ingestion.ingest_file(self.write('second/runbook.md', content))
        self.assertEqual(self.store.add_documents.call_args.kwargs['ids'], ids)
        ingestion.ingest_file(self.write('second/runbook.md', content.replace('operation', 'procedure')))
        self.assertNotEqual(self.store.add_documents.call_args.kwargs['ids'], ids)

    def test_chunk_ids_include_name_position_and_content(self):
        chunk = Document(page_content='Restart')
        make = ingestion._stable_chunk_id
        first = make(Path('one/runbook.md'), chunk, 0)
        self.assertEqual(first, make(Path('two/runbook.md'), chunk, 0))
        self.assertNotEqual(first, make(Path('other.md'), chunk, 0))
        self.assertNotEqual(first, make(Path('runbook.md'), chunk, 1))
        self.assertNotEqual(first, make(Path('runbook.md'), Document(page_content='Rollback'), 0))

    def test_partial_provider_failure_propagates_and_retry_reuses_ids(self):
        path = self.write('runbook.md', 'Restart safely. ' * 200)
        accepted = []
        def fail(chunks, ids):
            accepted.append(ids[0])
            raise RuntimeError('partial provider write')
        self.store.add_documents.side_effect = fail
        with self.assertRaisesRegex(RuntimeError, 'partial provider write'):
            ingestion.ingest_file(path)
        failed_ids = self.store.add_documents.call_args.kwargs['ids']
        self.store.add_documents.side_effect = None
        self.assertEqual(ingestion.ingest_file(path), len(failed_ids))
        self.assertEqual(self.store.add_documents.call_args.kwargs['ids'], failed_ids)
        self.assertEqual(accepted, [failed_ids[0]])

    def test_directory_filters_sorts_counts_and_does_not_recurse(self):
        self.write('b.TXT', 'B')
        self.write('a.md', 'A')
        self.write('ignored.csv', 'C')
        self.write('nested/hidden.md', 'D')
        with patch('src.ingestion.ingest_file', side_effect=[2, 3]) as ingest:
            self.assertEqual(ingestion.ingest_directory(self.root), 5)
            self.assertEqual([c.args[0].name for c in ingest.call_args_list], ['a.md', 'b.TXT'])
            self.assertEqual(ingestion.ingest_directory(self.root / 'absent'), 0)

    def test_directory_stops_after_first_failure(self):
        self.write('a.md', 'A')
        self.write('b.md', 'B')
        with patch('src.ingestion.ingest_file', side_effect=RuntimeError('failed')) as ingest:
            with self.assertRaisesRegex(RuntimeError, 'failed'):
                ingestion.ingest_directory(self.root)
            ingest.assert_called_once_with(self.root / 'a.md')


class VectorStoreTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings(openai_api_key='', pinecone_api_key='', embedding_dimension=3072)
        self.enterContext(patch('src.vectorstore.get_settings', return_value=self.settings))
        vectorstore.get_embeddings.cache_clear()
        self.addCleanup(vectorstore.get_embeddings.cache_clear)

    def test_missing_keys_fail_before_provider_calls(self):
        with patch('src.vectorstore.OpenAIEmbeddings') as embeddings, patch('src.vectorstore.Pinecone') as pinecone:
            with self.assertRaisesRegex(RuntimeError, 'OPENAI_API_KEY'):
                vectorstore.get_embeddings()
            with self.assertRaisesRegex(RuntimeError, 'PINECONE_API_KEY'):
                vectorstore.get_pinecone_client()
            embeddings.assert_not_called()
            pinecone.assert_not_called()

    def test_new_index_uses_embedding_dimension_and_configured_location(self):
        with patch('src.vectorstore.get_pinecone_client') as client:
            pc = client.return_value
            pc.list_indexes.return_value = []
            self.assertIs(vectorstore.ensure_index(), pc.Index.return_value)
            args = pc.create_index.call_args.kwargs
            self.assertEqual(args['dimension'], 3072)
            self.assertEqual(args['name'], self.settings.pinecone_index_name)
            self.assertEqual(args['metric'], 'cosine')
            self.assertEqual(args['spec'].asdict()['serverless'], {'cloud': 'aws', 'region': 'us-east-1'})

    def test_existing_index_dimension_and_provider_failures(self):
        with patch('src.vectorstore.get_pinecone_client') as client:
            pc = client.return_value
            pc.list_indexes.return_value = [SimpleNamespace(name=self.settings.pinecone_index_name)]
            for desc in [{'dimension': 3072}, SimpleNamespace(dimension=3072)]:
                pc.describe_index.return_value = desc
                self.assertIs(vectorstore.ensure_index(), pc.Index.return_value)
            pc.create_index.assert_not_called()
            pc.Index.reset_mock()
            pc.describe_index.return_value = {'dimension': 1536}
            with self.assertRaisesRegex(RuntimeError, 'dimension 1536'):
                vectorstore.ensure_index()
            pc.Index.assert_not_called()
            pc.list_indexes.side_effect = RuntimeError('provider unavailable')
            with self.assertRaisesRegex(RuntimeError, 'provider unavailable'):
                vectorstore.ensure_index()

    def test_store_and_retriever_share_configured_namespace_and_top_k(self):
        with patch('src.vectorstore.ensure_index') as index, patch('src.vectorstore.get_embeddings') as embeddings, patch('src.vectorstore.PineconeVectorStore') as store:
            vectorstore.get_retriever()
            store.assert_called_once_with(index=index.return_value, embedding=embeddings.return_value,
                                          namespace=self.settings.pinecone_namespace)
            store.return_value.as_retriever.assert_called_once_with(search_kwargs={'k': self.settings.top_k})
