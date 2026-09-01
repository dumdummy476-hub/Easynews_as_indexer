from __future__ import annotations

MOVIES = 2000
MOVIES_FOREIGN = 2010
MOVIES_OTHER = 2020
MOVIES_SD = 2030
MOVIES_HD = 2040
MOVIES_UHD = 2045
MOVIES_BLURAY = 2050
MOVIES_WEBDL = 2080
MOVIES_X265 = 2090
TV = 5000
TV_WEBDL = 5010
TV_FOREIGN = 5020
TV_SD = 5030
TV_HD = 5040
TV_UHD = 5045
TV_OTHER = 5050
TV_ANIME = 5070
TV_X265 = 5090
OTHER = 8000

PARENTS = {MOVIES, TV, OTHER}
CHILDREN = {
    MOVIES: {MOVIES_FOREIGN, MOVIES_OTHER, MOVIES_SD, MOVIES_HD, MOVIES_UHD, MOVIES_BLURAY, MOVIES_WEBDL, MOVIES_X265},
    TV: {TV_WEBDL, TV_FOREIGN, TV_SD, TV_HD, TV_UHD, TV_OTHER, TV_ANIME, TV_X265},
    OTHER: set(),
}


def category_matches(item_category: int, requested: set[int]) -> bool:
    if not requested:
        return True
    if item_category in requested:
        return True
    for parent in requested:
        if item_category in CHILDREN.get(parent, set()):
            return True
    return False


def caps_xml() -> str:
    return (
        '<categories>'
        '<category id="2000" name="Movies">'
        '<subcat id="2030" name="Movies/SD"/>'
        '<subcat id="2040" name="Movies/HD"/>'
        '<subcat id="2045" name="Movies/UHD"/>'
        '<subcat id="2050" name="Movies/BluRay"/>'
        '<subcat id="2080" name="Movies/WEB-DL"/>'
        '<subcat id="2090" name="Movies/x265"/>'
        '</category>'
        '<category id="5000" name="TV">'
        '<subcat id="5010" name="TV/WEB-DL"/>'
        '<subcat id="5030" name="TV/SD"/>'
        '<subcat id="5040" name="TV/HD"/>'
        '<subcat id="5045" name="TV/UHD"/>'
        '<subcat id="5070" name="TV/Anime"/>'
        '<subcat id="5090" name="TV/x265"/>'
        '</category>'
        '<category id="8000" name="Other"/>'
        '</categories>'
    )
