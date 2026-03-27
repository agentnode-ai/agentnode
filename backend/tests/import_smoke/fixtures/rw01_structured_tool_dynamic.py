"""Real-world: StructuredTool.from_function() with dynamic creation in loop.
Source: github.com/langchain-ai/langchain/issues/10778
"""
from langchain.tools import StructuredTool
from langchain.chains import RetrievalQA
from langchain.chat_models import ChatOpenAI
from langchain.embeddings.openai import OpenAIEmbeddings
from langchain.document_loaders import PyPDFLoader
from langchain.vectorstores import FAISS
from pydantic import BaseModel, Field


class DocumentInput(BaseModel):
    question: str = Field()


llm = ChatOpenAI(temperature=0, model="gpt-3.5-turbo-0613")

files = [
    {"name": "alphabet-earnings", "path": "/tmp/alphabet.pdf"},
    {"name": "tesla-earnings", "path": "/tmp/tesla.pdf"},
]

tools = []
for file in files:
    loader = PyPDFLoader(file["path"])
    pages = loader.load_and_split()
    embeddings = OpenAIEmbeddings()
    retriever = FAISS.from_documents(pages, embeddings).as_retriever()

    tools.append(
        StructuredTool.from_function(
            args_schema=DocumentInput,
            name=file["name"],
            description=f"useful for questions about {file['name']}",
            func=RetrievalQA.from_chain_type(llm=llm, retriever=retriever),
        )
    )
