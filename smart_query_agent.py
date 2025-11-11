import streamlit as st
import pandas as pd
import json
import time
from sqlalchemy import create_engine
import streamlit.components.v1 as components
import os

# --- 1. CONFIGURATION AND SECRETS ---

# Check for environment variables, assuming default values if not provided.
# NOTE: The Streamlit Cloud environment should provide these via the secrets.toml file.
try:
    POSTGRES_URL = st.secrets["connections"]["postgres_url"]
except KeyError:
    st.error("FATAL ERROR: PostgreSQL URL secret not found. Please ensure 'postgres_url' is set in Streamlit Secrets.")
    st.stop()

# Initialize API key and URL for Gemini
# NOTE: The canvas environment automatically handles the API key if left as ""
API_KEY = ""
API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-preview-09-2025:generateContent?key={API_KEY}"
MODEL_NAME = "gemini-2.5-flash-preview-09-2025"

# --- 2. DATABASE UTILITIES ---

# Placeholder for database connection engine
engine = None

@st.cache_resource
def get_db_engine(db_url):
    """Creates a SQLAlchemy engine for PostgreSQL."""
    try:
        engine = create_engine(db_url)
        # Attempt to establish a connection to validate the URL
        with engine.connect():
            st.success("Database connection established!")
        return engine
    except Exception as e:
        st.error(f"Error connecting to PostgreSQL. Check URL and credentials. Details: {e}")
        st.stop()

def get_table_schema(engine):
    """Fetches the schema of the main table to guide the LLM."""
    try:
        # Assuming the main table is named 'sales_data' based on previous context
        query = """
        SELECT 
            column_name, 
            data_type 
        FROM 
            information_schema.columns 
        WHERE 
            table_name = 'sales_data' 
            AND table_schema = 'public' 
        ORDER BY 
            ordinal_position;
        """
        schema_df = pd.read_sql(query, engine)
        
        if schema_df.empty:
            return "Table 'sales_data' not found or is empty."

        schema_string = "Table Name: sales_data\nColumns:\n"
        for index, row in schema_df.iterrows():
            schema_string += f"- {row['column_name']} ({row['data_type']})\n"
        
        return schema_string
    except Exception as e:
        return f"Error fetching schema: {e}"

# --- 3. LLM INTERFACE COMPONENT (FIXED) ---

@st.experimental_fragment
def llm_query_generator(user_query: str, db_schema: str, prompt_key: str):
    """
    Renders an HTML component to securely call the Gemini API for NL-to-SQL translation.
    """
    
    # System Instruction for the LLM
    system_instruction = f"""
    You are an expert PostgreSQL Query Generator for a Business Intelligence team.
    Your task is to convert a user's natural language request into a single, syntactically correct, and efficient PostgreSQL query.

    Database Schema:
    {db_schema}

    Instructions:
    1. STRICTLY output ONLY the final PostgreSQL query. Do not include any explanations, markdown formatting (like ```sql), or any text before or after the query.
    2. Always query the table named 'sales_data'.
    3. Use standard aggregate functions (SUM, AVG, COUNT, GROUP BY).
    4. If the user asks for a visualization or analysis, structure the query to return relevant columns for that analysis (e.g., a GROUP BY and an aggregate function).
    5. Ensure all column names and table names match the provided schema exactly.
    """
    
    # **CRITICAL FIX 1:** Sanitize the user query here in Python first (Fix for line ~102 error).
    # Escape double quotes for JS injection.
    sanitized_user_query = user_query.replace('"', '\\"')

    # **CRITICAL FIX 2:** Sanitize the system instruction (Fix for line ~110 error).
    # We must escape backslashes, backticks, and any dollar signs used in the instruction text for use in JS template literals.
    sanitized_sys_prompt = system_instruction.replace('\\', '\\\\').replace('`', '\\`').replace('$', '\\$')
    
    # --- JAVASCRIPT/HTML COMPONENT CODE ---
    html_code = f"""
    <script>
        const API_URL = "{API_URL}";
        const MODEL_NAME = "{MODEL_NAME}";
        // Injection of pre-sanitized user query
        const userQuery = "{sanitized_user_query}"; 
        const promptKey = "{prompt_key}";
        
        // Injection of pre-sanitized system instruction
        const sysPrompt = `{sanitized_sys_prompt}`;
        
        const payload = {{
            contents: [{{ parts: [{{ text: userQuery }}] }}],
            systemInstruction: {{ parts: [{{ text: sysPrompt }}] }},
            generationConfig: {{
                temperature: 0.1,
                maxOutputTokens: 2048,
            }},
        }};

        let retryCount = 0;
        const maxRetries = 5;
        const initialDelay = 1000; // 1 second

        async function callGeminiApi() {{
            while (retryCount < maxRetries) {{
                try {{
                    const response = await fetch(API_URL, {{
                        method: 'POST',
                        headers: {{ 'Content-Type': 'application/json' }},
                        body: JSON.stringify(payload)
                    }});

                    if (response.status === 429) {{
                        // Handle rate limiting with exponential backoff
                        const delay = initialDelay * Math.pow(2, retryCount);
                        console.warn(`Rate limit hit. Retrying in ${{delay}}ms...`);
                        await new Promise(resolve => setTimeout(resolve, delay));
                        retryCount++;
                        continue;
                    }}

                    if (!response.ok) {{
                        const errorData = await response.json();
                        throw new Error(`API Error: ${{response.status}} - ${{JSON.stringify(errorData)}}`);
                    }}

                    const result = await response.json();
                    const generatedText = result?.candidates?.[0]?.content?.parts?.[0]?.text || 'Error: No text generated.';

                    // Send the generated SQL query back to Streamlit
                    window.parent.postMessage({{
                        type: 'streamlit:setComponentValue',
                        key: promptKey,
                        value: generatedText.trim()
                    }}, '*');
                    return;

                }} catch (error) {{
                    console.error("Fetch failed:", error);
                    window.parent.postMessage({{
                        type: 'streamlit:setComponentValue',
                        key: promptKey,
                        value: `ERROR: API call failed. ${{error.message}}`
                    }}, '*');
                    return;
                }}
            }}
             // Max retries reached
            window.parent.postMessage({{
                type: 'streamlit:setComponentValue',
                key: promptKey,
                value: 'ERROR: Max API retries reached. Please try again later.'
            }}, '*');
        }}

        // Run the function when the component loads
        callGeminiApi();
    </script>
    <div id="loading_indicator" style="color: #6B7280; margin-top: 15px;">
        🧠 Analyzing request...
    </div>
    """
    
    # Render the HTML component
    return components.html(
        html_code,
        height=0,
        width=0,
        scrolling=False
    )

# --- 4. STREAMLIT APP LAYOUT AND LOGIC ---

st.set_page_config(layout="wide", page_title="LLM Data Analyst Agent")

# Header and Info
st.title("🤖 LLM Data Analyst Agent")
st.markdown("Ask natural language questions to analyze the company's sales data.")

# Connect to the Database
engine = get_db_engine(POSTGRES_URL)
db_schema = get_table_schema(engine)

# State Management for Query Generation
if 'sql_query' not in st.session_state:
    st.session_state.sql_query = ""
if 'query_in_progress' not in st.session_state:
    st.session_state.query_in_progress = False

# Function to start the LLM query process
def start_query(prompt_key):
    if st.session_state.user_prompt:
        st.session_state.query_in_progress = True
        # Call the fragment function to trigger the LLM call
        llm_query_generator(
            user_query=st.session_state.user_prompt,
            db_schema=db_schema,
            prompt_key=prompt_key
        )
    else:
        st.session_state.sql_query = "Please enter a question."

# Main interaction area
with st.container(border=True):
    st.text_area(
        "Enter your business question:",
        key="user_prompt",
        height=100,
        placeholder="Example: What was the total revenue for Software products in the APAC region last quarter?"
    )
    
    # Use a unique key for the component callback
    prompt_key = f"llm_output_{time.time()}" 
    
    st.button(
        "Generate SQL & Run Query", 
        on_click=start_query, 
        args=(prompt_key,), 
        type="primary",
        disabled=st.session_state.query_in_progress
    )

    # --- 5. QUERY EXECUTION AND DISPLAY ---

    # Check for output from the HTML component (SQL query or error message)
    if prompt_key in st.session_state and st.session_state.query_in_progress:
        sql_output = st.session_state[prompt_key]
        st.session_state.sql_query = sql_output
        st.session_state.query_in_progress = False # Query generation finished

# Display the Generated SQL Query
if st.session_state.sql_query and st.session_state.sql_query.startswith('SELECT'):
    st.subheader("Generated SQL Query:")
    st.code(st.session_state.sql_query, language="sql")
    
    # Execute the SQL query
    try:
        # Prevent injection of non-SELECT statements
        if not st.session_state.sql_query.strip().upper().startswith("SELECT"):
            raise ValueError("Only SELECT queries are permitted.")
        
        # Read data from the database
        result_df = pd.read_sql(st.session_state.sql_query, engine)
        
        st.subheader("Query Results:")
        
        if result_df.empty:
            st.warning("Query executed successfully but returned no results.")
        else:
            # Display results as a data table
            st.dataframe(result_df, use_container_width=True)

            # Attempt a basic chart if suitable
            if len(result_df.columns) == 2 and result_df.shape[0] > 1:
                col1, col2 = result_df.columns
                st.subheader(f"Visualization: {col2} by {col1}")
                try:
                    st.bar_chart(result_df, x=col1, y=col2)
                except Exception as chart_error:
                    st.error(f"Could not automatically generate chart: {chart_error}")

    except Exception as e:
        st.error(f"Error executing SQL query: {e}")
        st.code(st.session_state.sql_query, language="sql")

elif st.session_state.sql_query and st.session_state.sql_query.startswith('ERROR'):
    st.subheader("Error During Query Generation:")
    st.error(st.session_state.sql_query)

elif st.session_state.query_in_progress:
    # Display the loading indicator while the LLM is running
    with st.spinner("Analyzing request and generating SQL..."):
        time.sleep(0.5) # Wait for the component to initialize
        
# Display the schema for user reference (optional, for debugging/guidance)
with st.expander("View Database Schema (sales_data)"):
    if "Table Name" in db_schema:
        st.code(db_schema, language="markdown")
    else:
        st.error(f"Could not load schema: {db_schema}")
