function(ghostband_find_metal_tool variable tool)
  find_program(${variable} NAMES "${tool}" DOC "Path to the Metal ${tool} tool")
  if(NOT ${variable})
    execute_process(
      COMMAND /usr/bin/xcrun -f "${tool}"
      RESULT_VARIABLE result
      OUTPUT_VARIABLE tool_path
      OUTPUT_STRIP_TRAILING_WHITESPACE
      ERROR_QUIET
    )
    if(NOT result EQUAL 0 OR NOT EXISTS "${tool_path}")
      execute_process(
        COMMAND /usr/bin/xcrun -sdk macosx -f "${tool}"
        RESULT_VARIABLE result
        OUTPUT_VARIABLE tool_path
        OUTPUT_STRIP_TRAILING_WHITESPACE
        ERROR_QUIET
      )
    endif()
    if(NOT result EQUAL 0 OR NOT EXISTS "${tool_path}")
      message(FATAL_ERROR
        "Cannot find ${tool}. Install the Xcode Metal Toolchain or set ${variable}.")
    endif()
    set(${variable} "${tool_path}" CACHE FILEPATH "Path to the Metal ${tool} tool" FORCE)
  endif()
  if(NOT EXISTS "${${variable}}")
    message(FATAL_ERROR "${variable} does not exist: ${${variable}}")
  endif()
  message(STATUS "${variable}: ${${variable}}")
endfunction()

ghostband_find_metal_tool(GHOSTBAND_METAL_COMPILER metal)
ghostband_find_metal_tool(GHOSTBAND_METAL_LINKER metallib)
