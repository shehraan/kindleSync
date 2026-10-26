return {
    engine = "bing_rss",
    engines = {
        bing_rss = { name="bing_rss", display_name="Bing", max_results=10 },
        duckduckgo = {
            name = "duckduckgo", display_name = "DuckDuckGo",
            base_url = "https://lite.duckduckgo.com", search_path = "/lite/",
            language = "en", country = "ca", max_results = 10,
        },
    },
    render_type = "cre",
    cache_directory = nil,
    save_to_directory = nil,
    keep_old_website_files = true,
    download_images = false,
    use_stylesheets = false,
    search_highlighted_text = false,
    search_history_limit = 10,
    website_history_limit = 50,
    duplicate_entry_on_website_history = false,
}
