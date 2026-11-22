export function createAlternativeSearch({post, renderCard, refreshStates, getSelected}) {
  const list = document.getElementById("similar");
  const status = document.getElementById("similar-status");
  const more = document.getElementById("search-more-alternatives");
  let search = null;

  function reset() {
    search = null;
    list.replaceChildren();
    status.textContent = "";
    more.hidden = true;
    more.disabled = false;
  }

  async function fetchOffers(current, liveOnly) {
    status.textContent = "Searching Shopify…";
    more.disabled = true;
    try {
      const result = await post("/studio/scene/similar", {
        variant_id: current.item.variant_id,
        query: current.item.label.slice(0, 120),
        limit: liveOnly ? 12 : 9,
        ...(liveOnly ? {live_only: true, cursor: current.cursor} : {}),
      });
      if (search !== current || getSelected()?.id !== current.item.id) return;
      const added = [];
      for (const offer of result.results) {
        if (current.seen.has(offer.variant_id)) continue;
        current.seen.add(offer.variant_id);
        added.push(offer);
      }
      list.append(...added.map(renderCard));
      if (added.length) refreshStates(added.map((offer) => offer.variant_id));
      if (liveOnly) current.cursor = result.next_cursor;
      if (!liveOnly) status.textContent = added.length ? "" : "Shopify returned no alternatives.";
      else if (!current.cursor) {
        status.textContent = "No more Shopify results.";
        more.hidden = true;
      } else {
        status.textContent = added.length ? "" : "This page had no new alternatives.";
      }
    } catch (error) {
      if (search === current) status.textContent = "Could not load alternatives: " + error.message;
    } finally {
      if (search === current) more.disabled = false;
    }
  }

  async function load(item) {
    reset();
    if (!item.variant_id) {
      status.textContent = "This product has no Shopify variant to compare.";
      return;
    }
    const current = search = {item, cursor: null, seen: new Set([item.variant_id])};
    more.hidden = false;
    await fetchOffers(current, false);
  }

  more.addEventListener("click", () => {
    if (search && getSelected()?.id === search.item.id && !more.disabled) fetchOffers(search, true);
  });
  return {load, reset};
}
