"""Real-world: Tool() constructor pattern (not @tool, not BaseTool).
Source: openai-cookbook How_to_build_a_tool-using_agent_with_Langchain.ipynb
"""
from langchain.agents import Tool, initialize_agent, AgentType
from langchain.chains import RetrievalQA
from langchain.llms import OpenAI

retrieval_llm = OpenAI(temperature=0)

podcast_retriever = RetrievalQA.from_chain_type(
    llm=retrieval_llm,
    chain_type="stuff",
    retriever=None,  # would be docsearch.as_retriever() in notebook
)

tools = [
    Tool(
        name="Search",
        func=None,  # would be search.run
        description="useful for when you need to answer questions about current events",
    ),
    Tool(
        name="Knowledge Base",
        func=podcast_retriever.run,
        description="Useful for general questions about how to do things and for details on interesting topics.",
    ),
]

agent = initialize_agent(
    tools, retrieval_llm, agent=AgentType.ZERO_SHOT_REACT_DESCRIPTION, verbose=True
)
