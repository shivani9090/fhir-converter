import os, sys, asyncio
from pathlib import Path
from dotenv import load_dotenv
from langgraph.graph import StateGraph, START, END
from langchain_groq import ChatGroq
from langchain.agents import create_agent
from deepagents import create_deep_agent
from deepagents.backends.filesystem import FilesystemBackend
from deepagents.middleware.filesystem import FilesystemMiddleware, FilesystemPermission
from langfuse import get_client
from langfuse.langchain import CallbackHandler

from mcp_tools import get_graph_tools
from pr_review_graph import ReviewState, fetch_diff, post_comment

load_dotenv()
REPO_ROOT = Path(__file__).resolve().parents[1]
langfuse_handler = CallbackHandler()

async def make_review_node():
    graph_tools = await get_graph_tools()          # Neo4j MCP tools
    model = ChatGroq(model="openai/gpt-oss-120b", max_tokens=12000)

    backend = FilesystemBackend(root_dir=str(REPO_ROOT), virtual_mode=True)

    # Subagent is a plain agent with ONLY the Neo4j tools (no file tools), and the
    # graph shape is given up front so it never needs the large schema call.
    impact_runnable = create_agent(
        model,
        graph_tools,
        system_prompt=(
            "Find the impact of changed symbols using the Neo4j code graph only. "
            "Do NOT call get_neo4j_schema or apoc.meta.schema. Graph: "
            "(:File {path})-[:CONTAINS]->(:Symbol {name, qualified_name, kind, file, line, end_line}); "
            "(:Symbol)-[:CALLS]->(:Symbol); (:File)-[:CALLS]->(:Symbol); "
            "(:File)-[:IMPORTS]->(:File); (:Symbol)-[:INHERITS]->(:Symbol). "
            "For each symbol run one query for callers and one for dependents. "
            "Reply in at most 15 lines: file, symbol, why it is affected."
        ),
    )
    impact_analyst = {
        "name": "impact-analyst",
        "description": "Given changed functions/classes, returns their callers and dependents "
                       "from the Neo4j code graph.",
        "runnable": impact_runnable,
    }

    agent = create_deep_agent(
        model=model,
        tools=[],                                   # main agent delegates graph work
        system_prompt=(
            "You review pull requests for the FHIR Converter (CCDA/STU3 to FHIR R4). "
            "Use the github-pr-review skill. Plan with your todo list, delegate impact "
            "analysis to the impact-analyst subagent, then write one review ending with "
            "APPROVE-RECOMMENDED or CHANGES-REQUESTED. Read only the code around the "
            "changed lines (use offset/limit). Never repeat a secret's value."
        ),
        subagents=[impact_analyst],
        backend=backend,
        skills=["/.claude/skills/github-pr-review/"],                # folder that holds skill folders
        # Read-only file tools: drops write_file, edit_file and delete.
        middleware=[FilesystemMiddleware(backend=backend, tools=["ls", "read_file", "glob", "grep"])],
        # Block secrets and noisy folders; deny every write.
        permissions=[
            FilesystemPermission(operations=["read", "write"],
                                 paths=["/.env", "/.env.*", "/venv/**", "/.git/**", "/.playwright-mcp/**"],
                                 mode="deny"),
            FilesystemPermission(operations=["write"], paths=["/**"], mode="deny"),
        ],
    )

    async def review(state: ReviewState) -> dict:
        print('Agent review.')
        result = await agent.ainvoke(
            {"messages": [("user", f"Review this pull request diff:\n\n{state['diff']}")]}
        )
        return {"review": result["messages"][-1].content}
    return review

async def build_graph():
    g = StateGraph(ReviewState)
    g.add_node("fetch_diff", fetch_diff)
    g.add_node("review", await make_review_node())
    g.add_node("post_comment", post_comment)
    g.add_edge(START, "fetch_diff")
    g.add_edge("fetch_diff", "review")
    g.add_edge("review", "post_comment")
    g.add_edge("post_comment", END)
    return g.compile()

if __name__ == "__main__":
    async def main():
        sys.stdout.reconfigure(encoding="utf-8")
        app = await build_graph()
        out = await app.ainvoke(
            {"pr_number": int(sys.argv[1])},
            config={"callbacks": [langfuse_handler], "run_name": "pr-review-deepagent",
                    "metadata": {"langfuse_tags": ["pr-review", "deepagents"]}},
        )
        print(out["review"])
        get_client().flush()
    asyncio.run(main())
