import os
from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnablePassthrough
from langchain_community.utilities import SQLDatabase
from langchain_core.output_parsers import StrOutputParser
from langchain_groq import ChatGroq
import streamlit as st

# Load environment variables
load_dotenv()

# --- UTILITY FUNCTIONS ---

def init_database(user, password, host, port, database) -> SQLDatabase:
    db_uri = f"mysql+mysqlconnector://{user}:{password}@{host}:{port}/{database}"
    return SQLDatabase.from_uri(db_uri)

def validate_sql_query(query: str):
    valid_keywords = ["SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER", "WITH"]
    query = query.strip()
    # Remove markdown code blocks if the LLM includes them
    query = query.replace("```sql", "").replace("```", "").strip()
    
    if not any(query.upper().startswith(keyword) for keyword in valid_keywords):
        raise ValueError(f"Invalid SQL query generated: {query}")
    return query

def execute_sql_and_get_response(db, query):
    try:
        query = validate_sql_query(query)
        response = db.run(query)
        return response
    except Exception as e:
        return f"Error executing SQL query: {str(e)}"

# --- LANGCHAIN CHAINS ---

def get_sql_chain(db):
    template = """
    Based on the provided table schema and the user's question, generate a valid SQL query.
    Follow these rules:
    1. If the question is about column counts/names, use INFORMATION_SCHEMA.
    2. If the question is about record counts, use COUNT(*).
    3. Ensure the SQL query is returned as plain text without any markdown formatting.
    
    Schema: {schema}
    Question: {question}
    SQL Query:"""
    
    prompt = ChatPromptTemplate.from_template(template)
    
    # Get API Key safely
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise ValueError("GROQ_API_KEY not found. Please set it in your .env file or environment variables.")
    
    llm = ChatGroq(
        model="llama-3.3-70b-versatile",
        temperature=0,
        groq_api_key=api_key
    )

    def get_schema(_):
        return db.get_table_info()

    return (
        RunnablePassthrough.assign(schema=get_schema)
        | prompt
        | llm
        | StrOutputParser()
    )

def get_response(user_query: str, db: SQLDatabase, chat_history: list):
    praising_words = ["ok", "thank you", "thanks", "great", "awesome", "nice", "well done", "cool"]
    if any(word in user_query.lower() for word in praising_words):
        return "You're welcome! I'm here to help. 😊"

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        return "API key not configured. Please check your .env file."
    
    sql_chain = get_sql_chain(db)
    
    template = """
    You are an exceptional assistant. Based on the schema, question, SQL query, and SQL response, 
    generate a natural language answer that reflects the data accurately.
    
    Schema: {schema}
    Question: {question}
    SQL Query: {query}
    SQL Response: {response}

    Answer:"""
    
    prompt = ChatPromptTemplate.from_template(template)
    llm = ChatGroq(model="llama-3.3-70b-versatile", temperature=0, groq_api_key=api_key)

    chain = (
        RunnablePassthrough.assign(query=sql_chain).assign(
            schema=lambda _: db.get_table_info(),
            response=lambda vars: execute_sql_and_get_response(db, vars["query"]),
        )
        | prompt
        | llm
        | StrOutputParser()
    )
    
    try:
        return chain.invoke({"question": user_query, "chat_history": chat_history})
    except Exception as e:
        return f"Error in chain invocation: {str(e)}"

# --- STREAMLIT UI ---

st.set_page_config(page_title="Chat with MySQL", page_icon=":speech_balloon:")
st.title("Chat with MySQL")

# Initialize Chat History
if "chat_history" not in st.session_state:
    st.session_state.chat_history = [
        AIMessage(content="Hello! I'm a SQL assistant. Connect your database and ask away."),
    ]

# Sidebar Settings
with st.sidebar:
    st.subheader("Settings")
    host = st.text_input("Host", value="localhost")
    port = st.text_input("Port", value="3306")
    user = st.text_input("User", value="root")
    password = st.text_input("Password", type="password")
    database = st.text_input("Database")

    if st.button("Connect"):
        with st.spinner("Connecting..."):
            try:
                db = init_database(user, password, host, port, database)
                st.session_state.db = db
                st.success("Connected to database!")
            except Exception as e:
                # Check for specific MySQL errors and provide user-friendly messages
                error_message = str(e)
                if "1049" in error_message or "Unknown database" in error_message:
                    st.error("Database not found. Please check the database name and try again.")
                elif "1045" in error_message or "Access denied" in error_message:
                    st.error("Access denied. Please verify your username and password.")
                elif "2003" in error_message or "Can't connect" in error_message:
                    st.error("Cannot connect to the database server. Please check the host and port.")
                else:
                    st.error("Connection failed. Please verify your settings and try again.")
                # Optionally, log the full error for debugging (without showing to user)
                # import logging
                # logging.error(f"Database connection error: {error_message}")

# Display Chat History
for message in st.session_state.chat_history:
    if isinstance(message, AIMessage):
        with st.chat_message("AI"):
            st.markdown(message.content)
    elif isinstance(message, HumanMessage):
        with st.chat_message("Human"):
            st.markdown(message.content)

# Chat Input Logic
user_query = st.chat_input("Type a message...")
if user_query:
    st.session_state.chat_history.append(HumanMessage(content=user_query))
    
    with st.chat_message("Human"):
        st.markdown(user_query)

    with st.chat_message("AI"):
        # SAFETY CHECK: Ensure DB is connected
        if "db" not in st.session_state:
            error_msg = "Please connect to a database in the sidebar first!"
            st.warning(error_msg)
            st.session_state.chat_history.append(AIMessage(content=error_msg))
        else:
            try:
                response = get_response(user_query, st.session_state.db, st.session_state.chat_history)
                st.markdown(response)
                st.session_state.chat_history.append(AIMessage(content=response))
            except Exception as e:
                st.error(f"Error: {str(e)}")
