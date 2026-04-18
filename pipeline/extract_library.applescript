-- Dumps every track in the main Music library to stdout as delimited text.
-- Field separator: \x1f (Unit Separator). Record separator: \x1e (Record Separator).
-- These control characters never appear in music metadata, so no escaping is needed.
--
-- Fields (in order):
--   1. name
--   2. duration (seconds, real)
--   3. artist
--   4. album
--   5. played count (integer)
--   6. date added (ISO 8601 string)
--   7. played date (ISO 8601 string, empty if never played)
--   8. genre

on pad(n)
	set s to n as string
	if (count of s) < 2 then set s to "0" & s
	return s
end pad

on iso8601(d)
	set y to year of d as integer
	set m to (month of d as integer)
	set dd to day of d as integer
	set hh to hours of d as integer
	set mm to minutes of d as integer
	set ss to seconds of d as integer
	return (y as string) & "-" & pad(m) & "-" & pad(dd) & "T" & pad(hh) & ":" & pad(mm) & ":" & pad(ss)
end iso8601

on clean(s)
	-- Strip any stray control chars that could collide with our separators.
	if s is missing value then return ""
	set txt to s as string
	set AppleScript's text item delimiters to (ASCII character 31)
	set parts to text items of txt
	set AppleScript's text item delimiters to " "
	set txt to parts as string
	set AppleScript's text item delimiters to (ASCII character 30)
	set parts to text items of txt
	set AppleScript's text item delimiters to " "
	return parts as string
end clean

set FS to ASCII character 31
set RS to ASCII character 30
set rowList to {}

tell application "Music"
	set theTracks to (every track of library playlist 1 whose media kind is song)
	repeat with t in theTracks
		try
			set trkName to my clean(name of t)
			set trkDur to (duration of t) as string
			set trkArtist to my clean(artist of t)
			set trkAlbum to my clean(album of t)
			set trkPlays to (played count of t) as string
			set trkAdded to my iso8601(date added of t)
			set trkPlayed to ""
			try
				set pd to played date of t
				if pd is not missing value then set trkPlayed to my iso8601(pd)
			end try
			set trkGenre to my clean(genre of t)
			set end of rowList to trkName & FS & trkDur & FS & trkArtist & FS & trkAlbum & FS & trkPlays & FS & trkAdded & FS & trkPlayed & FS & trkGenre
		end try
	end repeat
end tell

set AppleScript's text item delimiters to RS
set output to rowList as string
set AppleScript's text item delimiters to ""
return output
