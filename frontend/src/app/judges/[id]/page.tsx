'use client';
import React, { useEffect, useState } from 'react';
import { TableContainer, Table, TableHead, TableRow, TableCell, TableBody, Paper, Box, Button } from '@mui/material';
import Layout from '@/components/Layout/Layout';
import { useParams } from 'next/navigation';
import { judge } from 'types/judge';
import { score } from 'types/score';




export default function ScorePosters() {
  // Define the initial values for each cell in the left column (strings)

  const { id } = useParams();

  useEffect(() => {
    async function fetchPosts() {
      const res = await fetch(`/api/judges/${id}/posters`)
      if (!res.ok) {
        alert("Error fetching this judges posters!")
        throw new Error('Error fetching judges posters');
    }
      const data: judge = await res.json()
      setJudgeName(`${data.first_name} ${data.last_name}`)
      setCellValues(data.scores)

    }
    fetchPosts()
  }, [id])


  const [judgeName, setJudgeName] = useState<string>("")
  const [submitEnabled, setSubmitEnabled] = useState<boolean>(false)

  const [cellValues, setCellValues] = useState<score[]>([]);

  // Handle button click to set ranking value
  const handleButtonClick = (rank: number, rowIndex: number) => {
    const updatedValues = [...cellValues];
    updatedValues[rowIndex].score = rank; // Set the ranking in the right column
    setCellValues(updatedValues);
  };


  useEffect(() => {
    // Check if all scores are not null
    const allScoresFilled = cellValues.every(score => score.score !== null);

    // Enable button only if all scores are filled
    setSubmitEnabled(allScoresFilled);
  }, [cellValues]); // The effect runs whenever the 'scores' state changes



  const handleSubmit = () => {

    const body = {
      scores: cellValues
    };
    fetch(`/api/judges/${id}/posters`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body),
    })
      .then(response => response.json())  
      .then(data => {
        setSubmitEnabled(false)
        alert("Scores submitted!")
        console.log(data);
      })
      .catch(error => {
        alert("Scores failed to update!")

        console.error('Error:', error);
      });

  };

  return (
    <Layout pageTitle={`Judge Posters: ${judgeName}`}>
      <Box sx={{ margin: '0 10%' }} paddingTop={'5%'}>
        <TableContainer component={Paper}>
          <Table>
            <TableHead>
              <TableRow>
                <TableCell></TableCell>
                <TableCell></TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {cellValues.map((row, rowIndex) => (
                <TableRow key={rowIndex}>
                  <TableCell>{row.poster.title}</TableCell>
                  <TableCell>
                    {row.poster.title !== "" && ( // Only show buttons if the left-hand cell is not an empty string
                      <Box display="flex" flexWrap="wrap" justifyContent="center">
                        {/* Render buttons for selecting rank 1 to 10 in two rows */}
                        {Array.from({ length: 10 }).map((_, index) => (
                          <Button
                            key={index}
                            variant={row.score === index + 1 ? 'contained' : 'outlined'} // Highlight the selected button
                            color="primary"
                            onClick={() => handleButtonClick(index + 1, rowIndex)} // Update rank on click
                            sx={{ margin: '0.5%' }} // Set width to fit buttons in two rows
                          >
                            {index + 1}
                          </Button>
                        ))}
                      </Box>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>

        {/* Centered Submit Button */}
        <Box sx={{ display: 'flex', justifyContent: 'center', marginTop: '20px' }}>
          <Button variant="contained" color="primary" onClick={handleSubmit} disabled={!submitEnabled}>
            Submit All
          </Button>
        </Box>
      </Box>
    </Layout>

  )
}
