import difflib
from typing import List, Dict, Any

import streamlit as st


def build_resource_data() -> List[Dict[str, Any]]:
    """Define a small in-memory database of GIX resources."""
    return [
        {
            "name": "Makerspace – Laser Cutter",
            "category": "Makerspace",
            "tags": ["laser", "laser cutter", "cutting", "fabrication"],
            "location": "Makerspace (Prototyping Labs)",
            "floor": "Ground floor labs",
            "hours": "Typical lab hours (see staff posting)",
            "description": "Laser cutter for precise 2D cutting and engraving of wood, acrylic, and other approved materials. Access usually requires safety training.",
        },
        {
            "name": "Makerspace – 3D Printers",
            "category": "Makerspace",
            "tags": ["3d printing", "3d printer", "prototyping", "makerspace"],
            "location": "Makerspace (Prototyping Labs)",
            "floor": "Ground floor labs",
            "hours": "Typical lab hours (see staff posting)",
            "description": "FDM and/or resin 3D printers for rapid prototyping of parts and enclosures. Bring your STL files and follow local print request procedures.",
        },
        {
            "name": "Hardware Prototyping Lab – PCB",
            "category": "Makerspace",
            "tags": ["pcb", "electronics", "soldering", "hardware lab"],
            "location": "Hardware Prototyping Lab",
            "floor": "Ground floor labs",
            "hours": "Typical lab hours (see staff posting)",
            "description": "Electronics and PCB lab with benches, test equipment, and tools for building and debugging custom hardware.",
        },
        {
            "name": "Main Conference Room – Classes",
            "category": "Classes & Events",
            "tags": ["classroom", "lecture", "class", "conference room"],
            "location": "Main conference room",
            "floor": "Main level",
            "hours": "Scheduled according to class timetable",
            "description": "Primary classroom space used for MS in Technology Innovation courses, reviews, and guest talks.",
        },
        {
            "name": "Program Meeting Spaces",
            "category": "Program & Admin",
            "tags": ["program meeting", "meeting", "advising"],
            "location": "Program offices",
            "floor": "1st floor",
            "hours": "Typical business hours or by appointment",
            "description": "Spaces where program staff hold advising sessions, cohort meetings, and administrative check-ins.",
        },
        {
            "name": "Phone Rooms",
            "category": "Study & Focus",
            "tags": ["phone room", "focus room", "zoom", "call"],
            "location": "Phone rooms on 1st & 2nd floors",
            "floor": "1st and 2nd floors",
            "hours": "Building open hours",
            "description": "Small enclosed rooms ideal for Zoom calls, interviews, or quiet focus work. Check local booking/usage rules.",
        },
        {
            "name": "Quiet Study Nooks",
            "category": "Study & Focus",
            "tags": ["quiet", "study", "focus", "nook"],
            "location": "Various corners near studios and hallways",
            "floor": "Throughout the building",
            "hours": "Building open hours",
            "description": "Informal seating areas and tucked-away corners that are good for quiet, individual work between classes.",
        },
        {
            "name": "Bike Storage",
            "category": "Transportation",
            "tags": ["bike", "bicycle", "bike storage", "commute"],
            "location": "Bike storage area (see building signage)",
            "floor": "Near ground level entry",
            "hours": "Typically building open hours; follow security guidance",
            "description": "Secure or semi-secure area for locking bikes while on campus. Bring your own lock and follow posted rules.",
        },
        {
            "name": "Free Printing",
            "category": "Student Services",
            "tags": ["printing", "printer", "free printing"],
            "location": "Near student work areas / computer stations",
            "floor": "Main student work areas",
            "hours": "Building open hours (subject to print policy)",
            "description": "Student-accessible printers for course materials and project documentation. Check your program guide for quotas and usage policy.",
        },
        {
            "name": "Nearby Cafe",
            "category": "Food & Coffee",
            "tags": ["food", "coffee", "cafe", "snacks"],
            "location": "Cafe just around the street from GIX",
            "floor": "Street level",
            "hours": "Typical daytime cafe hours",
            "description": "Casual cafe with coffee and light food options for quick breaks between classes.",
        },
        {
            "name": "Nearby Bar / Social Spot",
            "category": "Food & Coffee",
            "tags": ["bar", "drinks", "social", "food"],
            "location": "Bar just around the street from GIX",
            "floor": "Street level",
            "hours": "Evening hours (check local listing)",
            "description": "A nearby bar that students often use for socializing after project work or events.",
        },
    ]


def score_match(query: str, resource: Dict[str, Any]) -> float:
    """
    Compute a simple similarity score between the user query and a resource.

    This is a lightweight stand-in for an LLM: we look at keywords across
    name, category, tags, and description and compute a fuzzy match ratio.
    """
    if not query:
        return 1.0

    haystack_parts = [
        resource.get("name", ""),
        resource.get("category", ""),
        " ".join(resource.get("tags", [])),
        resource.get("description", ""),
    ]
    haystack = " ".join(haystack_parts).lower()
    q = query.lower()

    # Quick exact/substring boost
    if q in haystack:
        base = 1.0
    else:
        base = difflib.SequenceMatcher(None, q, haystack).ratio()

    # Gentle boost if any individual word matches strongly
    word_scores = []
    for word in q.split():
        word_scores.append(
            max(
                difflib.SequenceMatcher(None, word, token).ratio()
                for token in haystack.split()
            )
        )
    if word_scores:
        base = max(base, sum(word_scores) / len(word_scores))

    return base


def filter_and_rank_resources(
    resources: List[Dict[str, Any]], query: str, categories: List[str]
) -> List[Dict[str, Any]]:
    """
    Combine text search and category filters:

    - Text query controls how high something ranks.
    - Category filters gently boost matching categories but do not completely
      hide other resources, so both inputs work together.
    """
    filtered = []
    for r in resources:
        score = score_match(query, r)

        # If categories are selected, slightly boost resources in those
        # categories and slightly downweight the others, instead of
        # fully excluding them.
        if categories:
            if r["category"] in categories:
                score *= 1.2
            else:
                score *= 0.8

        # Apply a small threshold so obviously irrelevant items drop out
        if not query or score >= 0.25:
            r_with_score = dict(r)
            r_with_score["_score"] = score
            filtered.append(r_with_score)

    filtered.sort(key=lambda x: x["_score"], reverse=True)
    return filtered


def main() -> None:
    st.set_page_config(
        page_title="GIX Campus Wayfinder",
        page_icon="🧭",
        layout="wide",
    )

    st.title("GIX Campus Wayfinder")
    st.caption(
        "A simple prototype to help new MS in Technology Innovation students discover key campus resources."
    )

    with st.expander("What is this?", expanded=False):
        st.write(
            "This is a Week 1 prototype for exploring how new GIX students might "
            "search for spaces like the makerspace, bike storage, free printing, "
            "quiet study nooks, and nearby food options."
        )

    resources = build_resource_data()
    all_categories = sorted({r["category"] for r in resources})

    st.sidebar.header("Search & Filters")
    query = st.sidebar.text_input(
        "What are you looking for?",
        placeholder="e.g., '3D printing', 'quiet study', 'bike storage'",
    )
    selected_categories = st.sidebar.multiselect(
        "Filter by category (optional)", options=all_categories, default=[]
    )

    search_button = st.sidebar.button("Search")

    if search_button or (not query and not selected_categories):
        matches = filter_and_rank_resources(resources, query, selected_categories)
        if matches:
            st.subheader("Matching Resources")
            for res in matches:
                with st.container():
                    st.markdown(f"### {res['name']}")
                    meta = f"**Category:** {res['category']}  |  **Location:** {res['location']}  |  **Floor:** {res['floor']}"
                    st.markdown(meta)
                    st.write(res["description"])
                    with st.expander("Details"):
                        st.write("**Tags:**", ", ".join(res.get("tags", [])))
                        st.write("**Typical Hours:**", res["hours"])
        else:
            st.subheader("No matches found")
            st.write("Sorry, we couldn’t find what you were looking for :(")
            st.write(
                "Try different keywords or remove some filters. You can also search by broad terms like "
                "'makerspace', 'printing', 'quiet study', or 'food'."
            )
    else:
        st.info("Use the sidebar to enter a search term and choose filters, then press **Search**.")

if __name__ == "__main__":
    # Simple data integrity check
    resources = build_resource_data()
    names = [r["name"] for r in resources]
    assert len(names) == len(set(names)), "Duplicate resource names found in build_resource_data()"

    main()

