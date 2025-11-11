import streamlit as st
import pandas as pd
import hashlib 
import time
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError
from streamlit.components.v1 import html # Used to execute the client-side API call

# --- Configuration ---
TABLE_NAME = "sales_records"

# --- Core Database Functions ---

@st.cache_resource(ttl=3600) 
def get_db_connection():
    """Initializes and returns the SQLAlchemy Engine connection for PostgreSQL."""
    try:
        # Load the connection string securely from Streamlit secrets
        db_url = st.secrets["connections"]["postgres_url"]
        engine = create_engine(db_url)
        # Test the connection immediately
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return engine
    except Exception as e:
        st.error(f"❌ Connection Error: Could not connect to the centralized database. Please set the 'postgres_url' secret. Error: {e}")
        st.stop()


def execute_query(engine, sql_query, fetch=True):
    """Handles both SELECT (fetch=True) and INSERT/UPDATE (fetch=False) operations."""
    try:
        with engine.connect() as connection:
            if fetch:
                df = pd.read_sql(sql_query, connection)
                return df
            else:
                connection.execute(text(sql_query))
                connection.commit()
                return True
    except SQLAlchemyError as e:
        st.error(f"Database Error during execution: {e}")
        st.code(f"Failed Query: {sql_query}", language="sql")
        return pd.DataFrame() if fetch else False


def get_current_columns(engine):
    """Retrieves all column names currently in the sales_records table."""
    try:
        # PostgreSQL-specific query to get table columns
        sql = f"""
        SELECT column_name
        FROM information_schema.columns
        WHERE table_name = '{TABLE_NAME}'
        AND table_schema = 'public'
        ORDER BY ordinal_position;
        """
        df = execute_query(engine, sql)
        if df.empty:
            return []
        # Exclude internal tracking columns from schema given to the LLM
        return [col for col in df['column_name'].tolist() if col != 'record_hash']
    except Exception:
        # Default mandatory columns if the table doesn't exist yet
        return ['product_category', 'region', 'quarter', 'revenue', 'units_sold', 'client_name']

@st.cache_resource
def create_knowledge_base():
    """Creates the table if it does not exist."""
    engine = get_db_connection()
    try:
        # Check if the table exists and count records
        count_df = execute_query(engine, f"SELECT COUNT(*) as count FROM {TABLE_NAME}")
        return count_df['count'].iloc[0]
    except Exception:
        # If table is new, create it with mandatory columns
        create_sql = f"""
        CREATE TABLE {TABLE_NAME} (
            product_category TEXT,
            region TEXT,
            quarter TEXT,
            revenue FLOAT,
            units_sold INTEGER,
            client_name TEXT,
            record_hash VARCHAR(64) PRIMARY KEY
        );
        """
        execute_query(engine, create_sql, fetch=False)
        return 0

# Placeholder for Data Loading (Full logic omitted for brevity, but needed for file upload UI)
def load_uploaded_data(uploaded_file):
    st.info("Data loading logic omitted for this demonstration, but the database is ready.")
    time.sleep(1)
    st.success("Database structure confirmed. Please implement your full loading logic here.")

# --- LLM Integration Function ---

def construct_llm_prompt(query, schema):
    """Creates a detailed system prompt for the LLM to generate SQL."""
    
    # 1. System Instruction: Role, Rules, and Output Format
    system_instruction = (
        "You are an expert PostgreSQL data analyst. Your task is to translate a user's natural language query into a single, valid, production-ready SQL query. "
        "Strictly adhere to the following rules:\n"
        "1. **DO NOT** include any explanation, markdown formatting (like ```sql), or commentary. Output only the raw SQL statement.\n"
        "2. The table name is **sales_records**.\n"
        "3. Only use the column names provided in the schema below.\n"
        "4. Use standard SQL aggregate functions (SUM, AVG, COUNT) and filtering (WHERE, LIKE).\n"
        "5. For filtering text/categorical columns (like region, product_category), use single quotes (').\n"
        "6. If the query asks for a total or average, use an aggregate function and a GROUP BY clause if grouping is requested.\n"
        "7. Ensure your query is valid PostgreSQL syntax."
    )

    # 2. Schema Presentation
    schema_text = ", ".join(schema)
    
    # 3. User Query combined with context
    user_query = (
        f"The current table schema is: ({schema_text}).\n"
        f"The user wants the SQL query for this question: '{query}'"
    )
    
    return system_instruction, user_query

def llm_query_generator(query, schema):
    """
    Uses st.components.v1.html to call the Gemini API and generate a SQL query client-side.
    
    Returns the generated SQL query string.
    """
    # Use session state to avoid running the JS component on every single rerender
    if 'llm_sql_result' not in st.session_state:
        st.session_state.llm_sql_result = None

    if st.session_state.llm_sql_result is None or st.session_state.llm_query_hash != hash(query):
        st.session_state.llm_sql_result = "GENERATING"
        st.session_state.llm_query_hash = hash(query)
        
        system_instruction, user_query = construct_llm_prompt(query, schema)
        
        # Client-side JavaScript to perform the API call
        js_code = f"""
        <script>
        async function generateSql(systemPrompt, userQuery) {{
            const apiKey = ""; // Canvas provides this automatically
            const apiUrl = `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent?key=${{apiKey}}`;
            
            const payload = {{
                contents: [{{ parts: [{{ text: userQuery }}] }}],
                systemInstruction: {{ parts: [{{ text: systemPrompt }}] }},
                config: {{ temperature: 0.1 }}
            }};

            let responseText = "Error: Failed to generate SQL.";
            
            for (let i = 0; i < 3; i++) {{
                try {{
                    const response = await fetch(apiUrl, {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify(payload)
                    }});
                    
                    if (!response.ok) {{
                        throw new Error(`API returned status ${{response.status}}`);
                    }}
                    
                    const result = await response.json();
                    
                    if (result.candidates && result.candidates[0].content && result.candidates[0].content.parts) {{
                        responseText = result.candidates[0].content.parts[0].text.trim();
                        break;
                    }}
                }} catch (error) {{
                    console.error(`Attempt ${{i + 1}} failed: ${{error.message}}`);
                    await new Promise(resolve => setTimeout(resolve, Math.pow(2, i) * 1000));
                }}
            }}

            // Send the generated SQL back to Streamlit
            Streamlit.setComponentValue(responseText);
        }}

        if (window.Streamlit) {{
            // Escape quotes for JavaScript string
            const sysPrompt = `{system_instruction.replace(/"/g, '\\"')}`;
            const uQuery = `{user_query.replace(/"/g, '\\"')}`;
            generateSql(sysPrompt, uQuery);
        }}
        </script>
        """
        
        sql_result = html(
            js_code, 
            height=0, 
            width=0, 
            scrolling=False, 
            key=f"llm_sql_generator_{hash(query)}"
        )
        
        # This will hold the SQL when the component returns
        st.session_state.llm_sql_result = sql_result
        if sql_result and sql_result != "GENERATING":
            return sql_result
        else:
            return None # Component hasn't finished yet or returned None

    return st.session_state.llm_sql_result

# --- Streamlit Application Layout ---

def main():
    st.set_page_config(layout="wide", page_title="LLM Shared Data Query Agent")
    
    # Initialize component lib (Needed for st.components.v1.html)
    st.write('<script src="[https://cdn.jsdelivr.net/npm/@streamlit/component-lib@1.0.3/dist/streamlit-component-lib.js](https://cdn.jsdelivr.net/npm/@streamlit/component-lib@1.0.3/dist/streamlit-component-lib.js)"></script>', unsafe_allow_html=True)

    engine = get_db_connection() # Connects to PostgreSQL
    current_record_count = create_knowledge_base()
    current_columns = get_current_columns(engine)

    # Sidebar: Knowledge Base Management and Status
    with st.sidebar:
        st.header("🗄️ Shared Database Status")
        st.success("PostgreSQL Database Connected (Shared, LLM Ready)", icon="✅")
        st.metric(label="Total Records in DB", value=current_record_count)
        st.caption("All colleagues see the same data.")
        
        st.markdown("---")
        st.subheader("Current DB Schema")
        st.caption(", ".join(current_columns))
        
        st.markdown("---")
        st.subheader("Upload New Sales Data")
        uploaded_file = st.file_uploader(
            "Select CSV or Excel to append", 
            type=['csv', 'xlsx', 'xls']
        )
        if uploaded_file is not None:
            if st.button("💾 Add Data to DB", use_container_width=True, type="primary"):
                with st.spinner(f"Processing {uploaded_file.name}..."): 
                    load_uploaded_data(uploaded_file)
                st.rerun()

    # Main Content: Query Interface
    st.title("🧠 AI-Powered Data Query Agent")
    st.markdown(
        """
        Ask any complex question about your insurance sales data. The **Gemini LLM** will translate your request 
        into a valid SQL query against the shared PostgreSQL database.
        """
    )
    
    user_query = st.text_input(
        "Your Natural Language Query:", 
        placeholder="e.g., Show me the total revenue grouped by product category for Q1 2025 where client_name is not empty.",
        key="user_query"
    )

    if user_query:
        # Check if the query has been processed before or is actively being generated
        if st.session_state.get('llm_query_hash') != hash(user_query) or st.session_state.llm_sql_result == "GENERATING":
            # Show spinner while component is running
            with st.spinner("🧠 AI Agent is translating your request to SQL..."):
                generated_sql = llm_query_generator(user_query, current_columns)
                if generated_sql == "GENERATING" or generated_sql is None:
                    # Component is still running, stop execution and wait for callback
                    st.stop()
        else:
            generated_sql = st.session_state.llm_sql_result

        if generated_sql and generated_sql != "GENERATING":
            st.sidebar.caption(f"**LLM Output SQL:** `{generated_sql}`")
            
            if "error" in generated_sql.lower() or "fail" in generated_sql.lower():
                 st.error(f"❌ AI Translation Error: {generated_sql}")
                 return

            with st.spinner("⏳ Executing query on PostgreSQL..."):
                result_df = execute_query(engine, generated_sql)
            
            st.subheader("✅ Query Result")
            
            if result_df.empty:
                st.warning("No results found for that query in the Knowledge Base.")
            else:
                st.dataframe(result_df, use_container_width=True)
                
if __name__ == '__main__':
    main()