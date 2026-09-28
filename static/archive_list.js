/* The archive pages' shared list: search, results count and Load more.
 *
 * Used by /todo/completed, /notes/previous and /shopping/purchased. Each of
 * those pages is a server-paged list with the same toolbar and footer, and each
 * used to carry its own copy of this logic; one copy means search and paging
 * behave the same way on all three and are fixed in one place.
 *
 * Markup contract (the same element ids on every archive page):
 *
 *   #search-input, #search-btn, #search-clear   the toolbar's search group
 *   #search-results-count                       the count, hidden unless searching
 *   #load-more-container > #btn-load-more       the footer
 *
 * plus the list container, whose id the page passes in.
 *
 * The endpoint answers with an ArchivePage: {items, has_more, total}.
 *
 * The one rule every page shares is the one /todo/completed started with:
 * submitting a search clears the page's filter chips and resets its sort, and
 * Clear Search resets the whole page. A page supplies `resetFilters` to do its
 * half of that; a page with no chips or sort leaves it out.
 *
 * Search runs only on submit (the button or Enter), never on keystroke: it hits
 * the server, and paging has to agree with it about what the current query is
 * when Load more is pressed.
 */
(function (global) {
    'use strict';

    const PAGE_SIZE = 50;

    class ArchiveList {
        // opts:
        //   endpoint      '/api/…'                              (required)
        //   container     id of the list container               (required)
        //   noun          ['task', 'tasks'] for the count        (required)
        //   render        (items) => html                         (required)
        //   emptyMessage  () => string | null — null renders items anyway,
        //                 for a page that draws empty groups     (required)
        //   loadingText   placeholder while a fresh query loads   (required)
        //   errorText     shown when a request fails              (required)
        //   params        (URLSearchParams) => void — the page's own filters and sort
        //   resetFilters  () => void — clear chips, reset sort (no reload)
        //   onPage        (page) => void — called with each response, before render
        constructor(opts) {
            this.opts = Object.assign(
                { params: () => {}, resetFilters: () => {}, onPage: () => {} },
                opts
            );
            this.items = [];       // every page loaded so far, in server order
            this.hasMore = false;
            this.loading = false;
            this.searchTerm = '';  // the submitted term; '' when no search is active
            // Guards against a slow earlier request overwriting a newer one's results.
            this.requestToken = 0;

            this._el('search-input').addEventListener('input', () => this._updateSearchButton());
            this._el('search-input').addEventListener('keydown', (e) => {
                if (e.key === 'Enter') {
                    e.preventDefault();
                    this.submitSearch();
                }
            });
            this._el('search-btn').addEventListener('click', () => this.submitSearch());
            this._el('search-clear').addEventListener('click', () => this.clearSearch());
            this._el('btn-load-more').addEventListener('click', () => this.loadMore());
        }

        // Start over from the first page: for a new search, filter or sort.
        reload() {
            this.items = [];
            this._container().innerHTML =
                `<div class="container-loading-state">${this.opts.loadingText}</div>`;
            return this._fetchPage(0);
        }

        loadMore() {
            if (this.loading || !this.hasMore) return;
            return this._fetchPage(this.items.length);
        }

        // Reload what is already on screen, after the page changed something in
        // it. Refetching only the first page would bounce somebody working on
        // page three back to the top, so this asks for as many rows as are
        // loaded — in PAGE_SIZE pages, so the endpoint's `limit` cap holds.
        async refresh() {
            const token = ++this.requestToken;
            const wanted = Math.max(this.items.length, PAGE_SIZE);
            let items = [];
            let page = null;
            this._setLoading(true);
            try {
                do {
                    page = await this._request(items.length);
                    if (token !== this.requestToken) return; // superseded
                    items = items.concat(page.items);
                } while (page.has_more && items.length < wanted);
                this._accept(page, items);
            } catch (error) {
                if (token !== this.requestToken) return;
                this._fail(error);
            } finally {
                if (token === this.requestToken) this._setLoading(false);
            }
        }

        submitSearch() {
            const value = this._el('search-input').value.trim();
            if (!value) return;
            this.searchTerm = value;
            this.opts.resetFilters();
            return this.reload();
        }

        // Reset the page to its default state: no search, no filters, default sort.
        clearSearch() {
            this.searchTerm = '';
            this._el('search-input').value = '';
            this._updateSearchButton();
            this.opts.resetFilters();
            return this.reload();
        }

        // --- Internals ------------------------------------------------------

        async _fetchPage(offset) {
            const token = ++this.requestToken;
            this._setLoading(true);
            try {
                const page = await this._request(offset);
                if (token !== this.requestToken) return; // superseded
                this._accept(page, offset === 0 ? page.items : this.items.concat(page.items));
            } catch (error) {
                if (token !== this.requestToken) return;
                this._fail(error);
            } finally {
                if (token === this.requestToken) this._setLoading(false);
            }
        }

        async _request(offset) {
            const params = new URLSearchParams({ limit: PAGE_SIZE, offset });
            this.opts.params(params);
            if (this.searchTerm) params.set('search', this.searchTerm);
            const response = await fetch(`${this.opts.endpoint}?${params}`);
            if (!response.ok) throw new Error(`Failed to load ${this.opts.endpoint}`);
            return response.json();
        }

        _accept(page, items) {
            this.items = items;
            this.hasMore = page.has_more;
            this.opts.onPage(page);
            this._updateSearchCount(page.total);
            this.render();
        }

        _fail(error) {
            console.error(error);
            this.hasMore = false;
            this._container().innerHTML =
                `<div class="container-empty-state">${this.opts.errorText}</div>`;
        }

        // Re-draw what is loaded, e.g. after the page changed how a row reads.
        render() {
            if (this.items.length === 0) {
                const message = this.opts.emptyMessage();
                if (message != null) {
                    this._container().innerHTML =
                        `<div class="container-empty-state">${message}</div>`;
                    return;
                }
            }
            this._container().innerHTML = this.opts.render(this.items);
        }

        // Total matches across every page, not just the ones in hand.
        _updateSearchCount(total) {
            const el = this._el('search-results-count');
            if (!this.searchTerm) {
                el.hidden = true;
                return;
            }
            const [one, many] = this.opts.noun;
            el.hidden = false;
            el.textContent = total === 1 ? `1 matching ${one}.` : `${total} matching ${many}.`;
        }

        // The Search button is disabled unless the field holds non-whitespace text.
        _updateSearchButton() {
            this._el('search-btn').disabled = this._el('search-input').value.trim().length === 0;
        }

        _setLoading(loading) {
            this.loading = loading;
            const button = this._el('btn-load-more');
            this._el('load-more-container').style.display = this.hasMore ? '' : 'none';
            button.disabled = loading;
            button.textContent = loading ? 'Loading…' : 'Load more';
        }

        _container() {
            return this._el(this.opts.container);
        }

        _el(id) {
            return document.getElementById(id);
        }
    }

    global.ArchiveList = ArchiveList;
})(window);
